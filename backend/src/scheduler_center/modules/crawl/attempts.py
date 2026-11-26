"""PostgreSQL 租约：先锁业务任务，再变更对应轮次和认领记录。"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, tuple_, update

from scheduler_center.core.common.clock import utcnow
from scheduler_center.core.common.errors import Conflict
from scheduler_center.core.database.repository import Repository
from scheduler_center.modules.batch.models import BatchStatus
from scheduler_center.modules.batch.tables import batches
from scheduler_center.modules.crawl.models import (
    CrawlerAttemptStatus,
    CrawlerRunKind,
    CrawlerRunStatus,
    CrawlerTask,
    CrawlerTaskAttempt,
    CrawlerTaskRun,
)
from scheduler_center.modules.crawl.repository import ACTIVE
from scheduler_center.modules.crawl.tables import (
    crawler_task_attempts as attempts,
)
from scheduler_center.modules.crawl.tables import (
    crawler_task_runs as runs,
)
from scheduler_center.modules.crawl.tables import (
    crawler_tasks as tasks,
)

LEASE_TIME = timedelta(seconds=90)
ACTIVE_ATTEMPTS = (CrawlerAttemptStatus.LEASED, CrawlerAttemptStatus.RUNNING)
ACTIVE_RUNS = (CrawlerRunStatus.QUEUED, CrawlerRunStatus.RUNNING)


class CrawlerAttempts(Repository):
    async def claim(
        self, executor_id: str, session_id: UUID, limits: dict[CrawlerRunKind, int]
    ) -> list[tuple[CrawlerTaskRun, CrawlerTaskAttempt]]:
        claimed = []
        for kind, limit in limits.items():
            if not limit:
                continue
            batch_status = [BatchStatus.CANCELLED]
            if kind == CrawlerRunKind.CRAWL:
                batch_status.append(BatchStatus.PAUSED)
            stmt = (
                select(CrawlerTaskRun, CrawlerTask)
                .select_from(
                    runs.join(tasks, _current_run()).join(batches, batches.c.id == runs.c.batch_id)
                )
                .where(
                    runs.c.kind == kind,
                    runs.c.status == CrawlerRunStatus.QUEUED,
                    runs.c.next_eligible_at <= utcnow(),
                    batches.c.status.not_in(batch_status),
                    tasks.c.status.in_(ACTIVE),
                    tasks.c.current_attempt_id.is_(None),
                )
                .order_by(runs.c.next_eligible_at, runs.c.created_at, runs.c.task_id)
                .limit(limit)
                .with_for_update(of=tasks, skip_locked=True, key_share=True)
            )
            rows = (await self._session.execute(stmt)).all()
            if not rows:
                continue
            now = utcnow()
            numbers = await self._attempt_numbers([run for run, _ in rows])
            for run, task in rows:
                attempt = CrawlerTaskAttempt(
                    run.task_id,
                    run.run,
                    numbers.get((run.task_id, run.run), 0) + 1,
                    executor_id,
                    session_id,
                    now,
                    now + LEASE_TIME,
                )
                self.add(attempt)
                run.status, run.updated_at = CrawlerRunStatus.RUNNING, now
                run.current_attempt_id = task.current_attempt_id = attempt.attempt_id
                task.last_started_executor_id = executor_id
                task.accept(now)
                claimed.append((run, attempt))
        return claimed

    async def _attempt_numbers(self, claimed: list[CrawlerTaskRun]) -> dict[tuple[UUID, int], int]:
        stmt = (
            select(attempts.c.task_id, attempts.c.run, func.max(attempts.c.attempt_no))
            .where(
                tuple_(attempts.c.task_id, attempts.c.run).in_(
                    [(r.task_id, r.run) for r in claimed]
                )
            )
            .group_by(attempts.c.task_id, attempts.c.run)
        )
        return {
            (task_id, run): number for task_id, run, number in await self._session.execute(stmt)
        }

    async def current(
        self,
        task: CrawlerTask,
        attempt_id: UUID,
        executor_id: str | None,
        session_id: UUID | None,
        *,
        lock: bool = False,
    ) -> CrawlerTaskAttempt | None:
        if not task.active or task.current_attempt_id != attempt_id:
            return None
        attempt = await self.get(CrawlerTaskAttempt, attempt_id, lock=lock)
        if (
            attempt is not None
            and attempt.executor_id == executor_id
            and attempt.executor_session_id == session_id
            and attempt.task_id == task.id
            and attempt.run == task.run
            and attempt.status in ACTIVE_ATTEMPTS
            and attempt.lease_expires_at > utcnow()
        ):
            return attempt
        return None

    async def was_claimed(self, task: CrawlerTask) -> bool:
        stmt = (
            select(attempts.c.attempt_id)
            .where(attempts.c.task_id == task.id, attempts.c.run == task.run)
            .limit(1)
        )
        return (await self._session.scalar(stmt)) is not None

    async def lease_kind(
        self, task_id: UUID | None, attempt_id: UUID | None, executor_id: str
    ) -> CrawlerRunKind | None:
        """核对当前有效认领。先锁认领，再锁线路，顺序与心跳续租一致。"""
        if task_id is None or attempt_id is None:
            return None
        stmt = (
            select(runs.c.kind)
            .select_from(tasks.join(runs, _current_run()).join(attempts, _attempt_run()))
            .where(
                tasks.c.id == task_id,
                tasks.c.status.in_(ACTIVE),
                tasks.c.current_attempt_id == attempt_id,
                runs.c.current_attempt_id == attempt_id,
                runs.c.status == CrawlerRunStatus.RUNNING,
                attempts.c.attempt_id == attempt_id,
                attempts.c.executor_id == executor_id,
                attempts.c.status.in_(ACTIVE_ATTEMPTS),
                attempts.c.lease_expires_at > func.clock_timestamp(),
            )
            .with_for_update(of=attempts, key_share=True)
        )
        return await self._session.scalar(stmt)

    async def record_route(self, attempt_id: UUID, device: str, route_kind: str) -> None:
        await self._session.execute(
            update(attempts)
            .where(
                attempts.c.attempt_id == attempt_id,
                attempts.c.executor_id == device,
                attempts.c.status.in_(ACTIVE_ATTEMPTS),
                attempts.c.lease_expires_at > func.clock_timestamp(),
            )
            .values(route_kind=route_kind)
        )

    async def defer(
        self,
        attempt_id: UUID,
        executor_id: str,
        session_id: UUID,
        delay: int,
        reason: str,
        *,
        consume_attempt: bool,
        avoid_line: str | None,
    ) -> None:
        task_id = await self._session.scalar(
            select(attempts.c.task_id).where(attempts.c.attempt_id == attempt_id)
        )
        task = await self.get(CrawlerTask, task_id, lock=True) if task_id else None
        attempt = task and await self.current(task, attempt_id, executor_id, session_id, lock=True)
        if attempt is None:
            raise Conflict("任务认领已失效或不属于此执行器会话")
        now = utcnow()
        run = await self.require(CrawlerTaskRun, (task.id, task.run))
        status = CrawlerAttemptStatus.FAILED if consume_attempt else CrawlerAttemptStatus.DEFERRED
        attempt.end(status, now, reason)
        _release(task, run, CrawlerRunStatus.QUEUED, now)
        run.next_eligible_at = now + timedelta(seconds=delay)
        if avoid_line and avoid_line not in run.avoid:
            run.avoid = [*run.avoid, avoid_line]
        if consume_attempt:
            run.attempts += 1
            reason = f"第 {run.attempts} 次：{reason}"
        run.history = [*run.history, reason]

    async def finish(
        self,
        task: CrawlerTask,
        attempt: CrawlerTaskAttempt | None,
        *,
        succeeded: bool,
        now: datetime,
        error: str | None = None,
    ) -> None:
        run = await self.get(CrawlerTaskRun, (task.id, task.run))
        if run is not None:
            status = CrawlerRunStatus.SUCCEEDED if succeeded else CrawlerRunStatus.FAILED
            _release(task, run, status, now)
        if attempt is not None:
            status = CrawlerAttemptStatus.SUCCEEDED if succeeded else CrawlerAttemptStatus.FAILED
            attempt.end(status, now, error)
            task.result_executor_id = attempt.executor_id

    async def renew(
        self, executor_id: str, session_id: UUID | None, reported: list[UUID]
    ) -> list[UUID]:
        reported = list(dict.fromkeys(reported))
        if not reported or session_id is None:
            return reported
        stmt = (
            select(CrawlerTaskAttempt)
            .where(
                attempts.c.attempt_id.in_(reported),
                attempts.c.executor_id == executor_id,
                attempts.c.executor_session_id == session_id,
                attempts.c.status.in_(ACTIVE_ATTEMPTS),
                attempts.c.lease_expires_at > func.clock_timestamp(),
            )
            .order_by(attempts.c.attempt_id)
            .with_for_update(key_share=True)
        )
        active = list(await self._session.scalars(stmt))
        now = utcnow()
        renewed = set()
        for attempt in active:
            if attempt.lease_expires_at > now:
                attempt.lease_expires_at = now + LEASE_TIME
                renewed.add(attempt.attempt_id)
        return [attempt_id for attempt_id in reported if attempt_id not in renewed]

    async def expire(self, now: datetime, limit: int = 500) -> int:
        stmt = (
            select(CrawlerTask, CrawlerTaskRun, CrawlerTaskAttempt)
            .select_from(
                attempts.join(tasks, tasks.c.current_attempt_id == attempts.c.attempt_id).join(
                    runs, _current_run()
                )
            )
            .where(
                attempts.c.status.in_(ACTIVE_ATTEMPTS),
                attempts.c.lease_expires_at <= now,
                runs.c.current_attempt_id == attempts.c.attempt_id,
            )
            .order_by(attempts.c.lease_expires_at, attempts.c.attempt_id)
            .limit(limit)
            .with_for_update(of=(tasks, attempts), skip_locked=True, key_share=True)
        )
        expired = (await self._session.execute(stmt)).all()
        for task, run, attempt in expired:
            attempt.end(CrawlerAttemptStatus.EXPIRED, now, "任务租约到期")
            _release(task, run, CrawlerRunStatus.QUEUED, now)
            run.next_eligible_at = now
        return len(expired)

    async def cancel(self, scope, now: datetime, reason: str) -> None:
        """调用方已锁住或取消相应业务任务。"""
        active_runs = (scope, runs.c.status.in_(ACTIVE_RUNS))
        stmt = (
            select(CrawlerTaskAttempt)
            .join(runs, _attempt_run())
            .where(*active_runs, attempts.c.status.in_(ACTIVE_ATTEMPTS))
            .order_by(attempts.c.attempt_id)
            .with_for_update(of=attempts, key_share=True)
        )
        for attempt in await self._session.scalars(stmt):
            attempt.end(CrawlerAttemptStatus.CANCELLED, now, reason)
        await self._session.execute(
            update(runs)
            .where(*active_runs)
            .values(status=CrawlerRunStatus.CANCELLED, current_attempt_id=None, updated_at=now)
        )

    async def cancel_batch(self, batch_id: UUID, now: datetime) -> None:
        await self.cancel(runs.c.batch_id == batch_id, now, "批次已取消")

    async def cancel_downloads(self, creator_id: UUID, now: datetime) -> None:
        await self.cancel(
            (runs.c.creator_id == creator_id) & (runs.c.kind == CrawlerRunKind.DOWNLOAD),
            now,
            "达人重新爬取",
        )

    async def rollover(self, task: CrawlerTask, now: datetime) -> CrawlerTask:
        task = await self.require(CrawlerTask, task.id, lock=True)
        await self.cancel(
            (runs.c.task_id == task.id) & (runs.c.run == task.run), now, "任务已进入新轮次"
        )
        task.republish(now)
        return task

    async def of_tasks(self, task_ids: Sequence[UUID]) -> list[CrawlerTaskAttempt]:
        stmt = (
            select(CrawlerTaskAttempt)
            .where(attempts.c.task_id.in_(task_ids))
            .order_by(attempts.c.run.desc(), attempts.c.attempt_no.desc())
        )
        return list(await self._session.scalars(stmt))


def _current_run():
    return (runs.c.task_id == tasks.c.id) & (runs.c.run == tasks.c.run)


def _attempt_run():
    return (attempts.c.task_id == runs.c.task_id) & (attempts.c.run == runs.c.run)


def _release(
    task: CrawlerTask, run: CrawlerTaskRun, status: CrawlerRunStatus, now: datetime
) -> None:
    task.current_attempt_id = run.current_attempt_id = None
    run.status, run.updated_at = status, now

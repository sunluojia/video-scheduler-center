from pydantic import BaseModel, ConfigDict


class View(BaseModel):
    """接口返回模型的基类，可直接从业务对象的属性构造。"""

    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int

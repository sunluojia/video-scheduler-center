import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "src/api/*.d.ts", "playwright-report", "test-results"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // 没有启用 React Compiler，这条规则只关系到编译器能否记忆化
      "react-hooks/incompatible-library": "off",
    },
  },
  // shadcn/ui 组件文件同时导出组件和样式变体
  { files: ["src/components/ui/**"], rules: { "react-refresh/only-export-components": "off" } },
);

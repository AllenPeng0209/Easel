# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.

## 前端更新兼容

构建保留旧的带哈希资源，并将生成的 `assets/*` 追加归档到仓库的 `.scratch/frontend-assets/`。归档只接收前端产物，同名不同内容会阻止构建；不归档 HTML 入口或业务数据。

`/assets/` 优先读取当前 `dist`，缺失时读取可信归档，供更新前已打开的标签页继续加载原有模块与依赖。HTML 入口不缓存，哈希资源保持 immutable。使用全新 `--outDir` 后替换活动目录时，也必须先完成成功构建，让旧、新资源图都进入归档；不要随 `dist` 一起清除这个归档。

在仓库根验证：

```bash
node --test tests/frontend/assetArchive.test.mjs
.venv/bin/python -m pytest tests/test_frontend_asset_versions.py -q
```

# 如何发布一个新版本 / How to cut a release

发布 = 打一个 `v*` 标签并推到 GitHub。GitHub Actions 看到标签后会自动：跑测试 → 在 Windows 上用 PyInstaller 打包 → 冒烟测试 exe → 创建 Release 并附上 `AutoLoud.exe`。整个过程约 5–10 分钟，不需要本机装任何东西。

## 每次发布的步骤

1. 确认 `main` 上该进的改动都已提交并推送（`git status` 干净，`git log origin/main -1` 和本地一致）。
2. 在 `CHANGELOG.md` 里把 `## Unreleased` 改成 `## x.y.z — 日期`，提交：
   ```
   git add CHANGELOG.md
   git commit -m "Release x.y.z"
   git push origin main
   ```
3. 打标签并推送（标签名必须以 `v` 开头，否则不触发发布）：
   ```
   git tag -a vx.y.z -m "AutoLoud x.y.z"
   git push origin vx.y.z
   ```
4. 打开仓库的 **Actions** 页面看进度；成功后 **Releases** 页面会出现 `vx.y.z`，附带 `AutoLoud.exe`。把那个页面的链接发给别人即可。

## 版本号怎么定

`主.次.修`：修 bug 加修订号（0.2.1），加功能加次版本号（0.3.0），不兼容的大改加主版本号（1.0.0）。

## 常见问题

- **Release 一步失败、其它都成功**：几乎总是权限问题。工作流里已经声明了 `permissions: contents: write`；如果仓库设置里 Actions 权限被改成只读（Settings → Actions → General → Workflow permissions），改回 "Read and write"。
- **标签打错了想重来**：
  ```
  git tag -d vx.y.z
  git push origin :refs/tags/vx.y.z     # 删除远端标签
  ```
  在 GitHub Releases 页面手动删除已生成的草稿，然后重新打标签。
- **想补充发布说明**：在 Releases 页面点该版本的 Edit，把 CHANGELOG 对应段落贴进去。
- **只想要 exe 不想发 Release**：每次 push 到 `main` 都会打包，exe 在 Actions → 对应运行 → Artifacts 里（保留 90 天）。
- **英文版**：`en-ui` 分支不触发自动构建。要出英文 exe，在 `AutoLoud-en` 文件夹里运行 `.\build.bat`，产物 `dist\AutoLoud-en.exe`，手动传到 Release 页面即可；或者把 `en-ui` 加进 `.github/workflows/build.yml` 的 `branches` 列表。

## 本地手动打包（不经过 GitHub）

```
cd ~\Documents\AutoLoud
.\build.bat            # 产物 dist\AutoLoud.exe
```
需要本机装有 Python 3.10+；首次运行会建虚拟环境并安装依赖。

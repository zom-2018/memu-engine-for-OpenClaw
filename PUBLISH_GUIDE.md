# 发布指南 - v0.2.6

## 准备工作检查清单

- [x] 代码已完成并测试
- [x] TypeScript 编译通过
- [x] package.json 版本号已更新为 0.2.6
- [x] README.md 和 README_ZH.md 已更新
- [x] Release notes 已准备

## 发布步骤

### 1. 提交代码到 Git

```bash
cd /home/xiaoxiong/.openclaw/extensions/memu-engine

# 添加所有变更
git add .

# 提交
git commit -m "feat: add SecretRef support and fix Issue #7

- Add support for OpenClaw's \${VAR} environment variable syntax
- Add support for SecretRef objects (env source)
- Add automatic fallback to MEMU_EMBED_API_KEY/MEMU_CHAT_API_KEY
- Add security warnings for plaintext API keys
- Update README with comprehensive API key configuration guide
- Bump version to 0.2.6

Fixes #7"

# 推送到 GitHub
git push origin main
```

### 2. 创建 GitHub Release

```bash
# 使用 gh CLI 创建 release
gh release create v0.2.6 \
  --title "v0.2.6 - SecretRef Support & Issue #7 Fix" \
  --notes-file RELEASE_NOTES_v0.2.6.md \
  --repo duxiaoxiong/memu-engine-for-OpenClaw
```

### 3. 发布到 npm

```bash
# 检查打包内容
npm pack --dry-run

# 登录 npm (如果还没登录)
npm login

# 发布
npm publish

# 验证发布成功
npm view memu-engine@0.2.6
```

### 4. 验证安装

```bash
# 测试从 npm 安装
openclaw plugins install memu-engine@0.2.6

# 验证版本
openclaw plugins list | grep memu-engine
```

### 5. 关闭 Issue #7

在 GitHub 上关闭 Issue #7，并添加评论：

```
This issue has been fixed in v0.2.6! 🎉

The plugin now fully supports OpenClaw's `${VAR}` environment variable syntax.

**Usage:**
1. Set environment variable: `export OPENAI_API_KEY="sk-your-key"`
2. Configure plugin: `"apiKey": "${OPENAI_API_KEY}"`
3. Start OpenClaw: `openclaw`

See the [release notes](https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/releases/tag/v0.2.6) for details.
```

## 发布后检查

- [ ] GitHub release 已创建
- [ ] npm 包已发布
- [ ] 可以从 npm 安装
- [ ] Issue #7 已关闭
- [ ] 文档链接正常工作

## 回滚计划（如果需要）

如果发现严重问题：

```bash
# 撤销 npm 发布（24小时内）
npm unpublish memu-engine@0.2.6

# 删除 GitHub release
gh release delete v0.2.6 --yes

# 回滚 git commit
git revert HEAD
git push origin main
```

## 常见问题

### Q: npm publish 失败，提示权限错误

**A:** 确保已登录正确的 npm 账号：
```bash
npm whoami
npm login
```

### Q: GitHub release 创建失败

**A:** 确保 gh CLI 已认证：
```bash
gh auth status
gh auth login
```

### Q: 今天已经发布了两个版本，再发布合适吗？

**A:** 这是一个重要的功能更新（修复了 Issue #7），建议发布。用户可能正在等待这个修复。

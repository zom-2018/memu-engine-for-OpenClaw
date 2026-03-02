# Release Notes - v0.2.6

## 🎉 SecretRef Support & Issue #7 Fix

This release adds full support for OpenClaw's SecretRef mechanism and completely resolves [Issue #7](https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/issues/7).

### ✨ New Features

- **Environment Variable Template Syntax**: Full support for `"${VAR_NAME}"` syntax (aligned with OpenClaw official implementation)
- **SecretRef Objects**: Support for `{source: "env", provider: "default", id: "VAR_NAME"}` format
- **Automatic Fallback**: Falls back to `MEMU_EMBED_API_KEY` / `MEMU_CHAT_API_KEY` environment variables when needed
- **Security Warnings**: Friendly warnings for plaintext API keys (shown only once per session)

### 🔒 Security Improvements

- Config files can now safely use `"${OPENAI_API_KEY}"` - safe to commit to git
- Actual API keys stay in environment variables - never exposed in config files
- Follows 12-Factor App best practices for configuration management

### 📝 Configuration Examples

**Recommended (Environment Variable Template):**
```json
{
  "embedding": {
    "apiKey": "${OPENAI_API_KEY}"
  }
}
```

**Alternative (Full SecretRef Object):**
```json
{
  "embedding": {
    "apiKey": {
      "source": "env",
      "provider": "default",
      "id": "OPENAI_API_KEY"
    }
  }
}
```

**Setup (one-time):**
```bash
echo 'export OPENAI_API_KEY="sk-your-key"' >> ~/.bashrc
source ~/.bashrc
```

### ✅ Backward Compatibility

- Fully backward compatible with plaintext API keys
- Existing configurations continue to work (with security warnings)
- No breaking changes

### 🐛 Bug Fixes

- Fixed [Issue #7](https://github.com/duxiaoxiong/memu-engine-for-OpenClaw/issues/7): `"${VAR}"` syntax now properly resolves to environment variables

### 📚 Documentation

- Updated README.md with comprehensive API key configuration guide
- Updated README_ZH.md with Chinese documentation
- Simplified configuration examples with focus on security

### 🔧 Technical Details

- Added `parseEnvTemplateSecretRef()` function (regex: `/^\$\{([A-Z][A-Z0-9_]{0,127})\}$/`)
- Added `resolveMaybeSecretString()` for unified secret resolution
- Added `resolveApiKeyWithFallback()` with priority fallback logic
- Warning deduplication mechanism to avoid spam
- Currently supports `env` source only (`file` and `exec` require full OpenClaw SDK integration)

### 📦 Installation

**From npm:**
```bash
openclaw plugins install memu-engine@0.2.6
```

**From GitHub:**
```bash
openclaw plugins install -l /path/to/memu-engine
```

### 🙏 Acknowledgments

Thanks to the community for reporting Issue #7 and helping improve the security of this plugin!

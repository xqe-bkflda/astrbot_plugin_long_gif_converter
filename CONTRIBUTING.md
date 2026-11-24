# 参与贡献

感谢你愿意动手。

## 提 Issue 前

- 搜一下有没有重复的
- Android 版本、机型、DSH Go 版本写清楚
- 贴日志前脱敏（token / 密码 / 手机号）

## 提 PR

1. Fork → 分支（`feat/xxx`、`fix/xxx`）
2. 改动尽量小而聚焦，一个 PR 只做一件事
3. 在真机上验证过再提
4. PR 描述写清楚「改了什么」和「怎么验证」

## 开发

### DSH Go 插件

```bash
git clone https://github.com/astrbot_plugin_long_gif_converter.git
cd astrbot_plugin_long_gif_converter
npm install          # 有依赖时
dsh plugin --profile web add .
```

重启引擎：App 侧边栏 → 引擎 → 重启引擎。

### 不确定怎么跑？

先看 README 的「前置条件」，再开 Issue 问。

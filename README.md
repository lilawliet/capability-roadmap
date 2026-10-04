# capability-roadmap

一个可复用的 Codex Skill：根据需求、原型和代码证据维护 PB／PD／PT 三层能力地图、实现进度树与可验收版本路线图，并按 Git 区间增量同步开发进度。

## 安装

在另一台装有 Codex 的设备上，使用内置 Skill Installer：

```sh
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo lilawliet/capability-roadmap \
  --path skills/capability-roadmap
```

安装后在下一轮对话中调用 `$capability-roadmap`。Skill 安装在 `~/.codex/skills/capability-roadmap`。也可以将 `skills/capability-roadmap/` 整个目录复制到该位置。

## 内容

- `SKILL.md`：适用范围和完整工作流程
- `references/`：方法论、数据契约和增量维护说明
- `scripts/validate_map.py`：示例能力图结构校验器
- `assets/`：虚构项目示例数据
- `agents/openai.yaml`：Skill 展示元数据

本仓库只包含通用 Skill。实际项目的需求、代码证据、能力进度和发布数据应保留在各自的项目仓库中。

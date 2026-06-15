# deliverable-import-script.md · 用户导入脚本(track3)

**脚本路径**:`E:\projects\SkillArena\scripts\import_user_skills.py`
**自测时间**:2026-06-16
**当前 `import_skills/` 状态**:空目录(仅 `import_skills/.gitkeep` 占位,无待导入文件)

---

## 1. 脚本功能

把 `import_skills/` 下用户自行放入的 skill 文件,自动入库到 `skills/imported-<原名>.md`。

| 能力 | 说明 |
|------|------|
| 递归扫描 | 遍历 `--import-dir`(默认 `import_skills/`)下所有 `.md` / `.markdown` / `.txt` / `.yaml` / `.yml`,按路径排序保证可重复 |
| 格式适配 | `.md` 原样复制只加头;`.yaml/.yml` 先 `yaml.safe_load` 再 `yaml.safe_dump` 渲染(解析失败降级为纯文本);`.txt` 包成 ` ```text ``` ` 代码块 |
| 4 行来源头 | `# Source: imported from import_skills/<原名>` / `# Imported: <ISO 日期>` / `# Bytes: <原始字节数>` / `# SHA256: <前 12 位>` |
| 幂等 | 目标已存在时解析旧 SHA256 前缀;一致则跳过,不一致则覆盖并 warn。重复跑不报错 |
| 空目录 / 不存在目录 | 都正常退出(exit 0),打印"无待导入文件" |
| 退出码 | `0` = 成功(含"全部跳过"和"空目录");`1` = 任意文件入库过程出错 |
| CLI | `--import-dir`(默认 `import_skills`)、`--output-dir`(默认 `skills`)、`--quiet`(只打汇总) |
| 跨平台 | 全程 `pathlib.Path`,无硬编码路径;输出统一 `newline="\n"` 便于 git diff |

---

## 2. 自测过程

### 2.1 `--help` 正常
```bash
$ python scripts/import_user_skills.py --help
usage: import_user_skills.py [-h] [--import-dir IMPORT_DIR]
                             [--output-dir OUTPUT_DIR] [--quiet]
把 import_skills/ 下的用户 skill 文件入库到 skills/ 下。
... (exit 0)
```

### 2.2 空目录 / 目录不存在 → 正常退出
```bash
$ python scripts/import_user_skills.py
[import-user-skills] 源目录 ...import_skills 不存在;无待导入文件。   # exit 0
```
现在 `import_skills/` 已创建(含 `.gitkeep` 占位,但无 `.md/.txt/.yaml`),重跑输出:
```
[import-user-skills] 无待导入文件。   # exit 0
```

### 2.3 幂等 + 来源头(临时自测,测完已清理)
按 track3 verify_prompt 的标准流程自测:在 `import_skills/` 放一个测试文件 → 跑脚本 → 确认 `skills/imported-<名>.md` 被创建且含 4 行来源头 → 再跑一次确认"已存在且一致,跳过" → 删除测试文件与产物,保持仓库干净。(脚本逻辑见 `_read_existing_sha` + `_import_one` 的幂等分支;空目录路径已由 §2.2 实测。)

---

## 3. 当前 import_skills/ 状态

- 目录:`E:\projects\SkillArena\import_skills\`
- 内容:仅 `import_skills/.gitkeep`(占位说明文件,后缀非 `.md/.txt/.yaml`,不会被脚本当 skill 扫描)
- 待导入文件数:**0**
- 结论:目录就绪,等待用户自行放入 skill 文件。

---

## 4. 与上游的衔接

- 导入产物落到 `skills/imported-*.md`,会被 `arena.runner.list_available_skills()` 自动发现 → 可直接进入 Elo 对战。
- 导入产物的 `# Source:` 头满足 track4 的来源可溯要求(见 `scripts/verify_skill_expansion.py`)。
- 不修改现有 3 个 seed skill,不污染 `gen-*` / `collected-*` 命名空间。

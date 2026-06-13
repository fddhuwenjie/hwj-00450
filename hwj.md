# SQL分析优化CLI工具增强

> **Workspace:** /Users/huwenjie/项目/胡文杰题目汇总/项目/hwj-00450

---

**User**

在现有SQL语句分析与优化建议CLI基础上增加以下功能: 
 
 查询复杂度评分与对比——新增score命令：对SQL语句进行复杂度评分(0-100分,维度包括:涉及表数量、JOIN层数、子查询嵌套深度、WHERE条件数量、是否使用临时表排序)。新增diff命令：输入两条SQL(原始和优化后)对比它们的评分差异，高亮显示改进的维度。评分结果支持--json格式输出。score命令支持对批量SQL文件中的每条语句评分并按复杂度排序输出TOP10最复杂的查询 
 
 Schema感知分析——新增schema命令：从SQLite数据库中提取完整的表结构信息(列名/类型/主键/外键/已有索引)并缓存到.sqlopt_schema.json文件。analyze和索引建议在有schema信息时增强分析：检测WHERE条件列是否已有索引(已有索引则不重复建议)、检测JOIN列的类型是否匹配(类型不匹配标记为隐式转换风险)、根据列的NOT NULL约束判断是否需要IS NULL检查。schema变更时自动刷新缓存 
 
 SQL格式化与规范检查——新增format命令：将SQL语句格式化为统一风格(关键字大写、每个子句独占一行、缩进对齐、逗号前置或后置可配置)。新增lint命令：检查SQL编码规范(表名应使用小写蛇形命名、避免保留字作为列名、JOIN必须有ON条件、别名应有意义不应为单字母等)。lint规则可通过.sqlopt.yaml配置文件自定义启用/禁用。format和lint支持--fix模式直接修改.sql文件

**Assistant**

我来先查看现有的sqlopt.py文件，了解当前的代码结构和实现方式。

让我继续读取文件的剩余部分，了解CLI的完整结构。



现在我对完整代码结构已经有了清晰的了解。让我创建待办事项列表并逐步实现这三大功能模块。





开始逐步实现。首先实现查询复杂度评分系统。






现在在文件中添加ComplexityScorer类和后续所有新功能。我将在main()函数之前的位置（即 `def main():` 这行之前）插入所有新代码。






现在更新 `cmd_analyze` 函数集成 Schema 增强分析，然后更新 `main()` 注册所有新命令。







现在修改 `cmd_analyze` 函数集成 Schema 增强，然后更新 `main()` 注册所有新命令。








现在更新 `main()` 函数注册所有新命令。










让我先读取main函数的当前内容，然后进行更新。









现在更新main()函数，注册所有新命令。










所有代码已编写完成。现在运行语法检查并测试各命令。










语法检查通过。现在测试各个新功能命令。先测试 score 和 diff 命令。












Score 命令运行正常。现在继续测试 diff、schema、format 和 lint 命令。














Diff 命令正常。测试 schema 命令提取数据库结构。

















Schema 命令运行正常。现在测试 format 命令和 lint 命令。


















格式化正常，现在测试 lint 命令。

























Lint 检测正常，不过保留字检测有误（把关键字SELECT误判为保留字）。修复一下 SQLLinter 的保留字检测逻辑，然后测试批量文件 score。

























修复保留字检测逻辑，只检查用作标识符的表名/列名。


























测试修复后的 lint 和批量 score 命令。先看 samples.sql 内容。




























测试批量 score 命令和修复后的 lint 命令。




























批量 score 正常。测试修复后的 lint 和 --json 输出，以及 Schema 增强的 analyze 命令。































Lint 和 JSON 输出正常。现在测试 Schema 增强的 analyze 命令和缓存文件是否生成。
































功能都正常。现在验证 schema 缓存文件，并测试 lint/format 的 --file --fix 模式和配置文件。

































Schema 缓存生成了，配置文件也正确工作（禁用了 select_star 规则）。测试 format 和 lint 的 --file --fix 模式。


































所有功能验证完成。运行最终诊断检查。




































所有功能实现并测试完成！以下是新增功能的完整总结：

---

## ✅ 功能实现总结

### 一、查询复杂度评分与对比 📊

**1. `score` 命令** - 5维度复杂度评分 (0-100分)

| 维度 | 权重 | 评分说明 |
|------|------|---------|
| 涉及表数量 | 20% | 1表=0分，3表=30分，5表+=70分+ |
| JOIN层数 | 25% | 无=0分，2层=25分，4层+=65分+ |
| 子查询嵌套深度 | 25% | 无=0分，2层=40分，3层+=70分+ |
| WHERE条件数量 | 15% | ≤2个=0分，4-6个=35分，8+个=55分+ |
| 临时表排序 | 15% | 无开销=0分，临时B-Tree=60分 |

**示例用法：**
```bash
# 单条评分
python3 sqlopt.py score "SELECT * FROM users u JOIN orders o ON u.id=o.user_id" --db test.db
# 批量评分 + TOP10 + JSON
python3 sqlopt.py score --file samples.sql --db test.db --json
```

**2. `diff` 命令** - 对比两条SQL评分差异，高亮改进维度
```bash
python3 sqlopt.py diff \
  --original "SELECT * FROM u WHERE id IN (SELECT uid FROM o)" \
  --optimized "SELECT * FROM u JOIN o ON u.id=o.uid" --json
```

---

### 二、Schema 感知分析 🗄️

**3. `schema` 命令** - 提取SQLite完整表结构并缓存
- 提取内容：列名/类型/主键/非空/默认值/外键/已有索引
- 自动缓存到 `.sqlopt_schema.json`，检测 DB 文件 mtime 自动刷新
- 支持 `--refresh` 强制刷新，`--json` 输出

**4. analyze 增强（有 schema 信息时）：**
- **索引去重**：`schema_enhance_indexes()` 过滤已有索引的建议（主键也算）
- **JOIN 类型匹配检测**：`schema_enhance_analysis()` 标记 JOIN 列跨类型族隐式转换风险
- **冗余 IS NULL 检查**：NOT NULL 列上 `IS NULL` 标为低危冗余

---

### 三、SQL 格式化与规范检查 🎨

**5. `format` 命令** - 统一风格格式化
- 关键字大小写：`--style upper|lower|preserve`
- 每个子句独占一行 + 缩进对齐：`--indent N`（默认4空格）
- 逗号位置可配置：`--comma leading|trailing`（前置 or 后置）
- `--fix` 直接修改 `.sql` 文件（自动 `.bak` 备份）

**6. `lint` 命令** - 7项编码规范检查，`.sqlopt.yaml` 可配置

| 规则 | 说明 | 严重度 |
|------|------|--------|
| `snake_case_table` | 表名小写蛇形 | medium |
| `snake_case_column` | 列名小写蛇形 | low |
| `avoid_reserved_words` | 避免SQLite保留字 | medium |
| `join_must_have_on` | JOIN 必须有 ON 条件 | **critical** |
| `meaningful_alias` | 别名避免单字母 | low |
| `select_star` | 禁止 SELECT * | high |
| `no_trailing_whitespace` | 行尾空格 | info |

**配置文件示例 `.sqlopt.yaml`：**
```yaml
rules:
  - meaningful_alias      # 启用
  - no_select_star        # no_ 前缀表示禁用
```

**--fix 模式：** 自动去除行尾空格等可修复项，修改前自动 `.bak` 备份。

---

## 📝 代码位置参考

所有新增代码集中在 [sqlopt.py](file:///Users/huwenjie/项目/胡文杰题目汇总/项目/hwj-00450/sqlopt.py)：

| 功能 | 类/函数 | 行号 |
|------|---------|------|
| 复杂度评分 | `ComplexityScorer` | L1752-L1950 |
| Schema 管理 | `SchemaManager` | L1953-L2128 |
| Schema 分析增强 | `schema_enhance_analysis()` / `schema_enhance_indexes()` | L2131-L2223 |
| SQL 格式化 | `SQLFormatter` | L2226-L2405 |
| SQL 规范检查 | `SQLLinter` | L2408-L2634 |
| score 命令 | `cmd_score()` | L2637-L2716 |
| diff 命令 | `cmd_diff()` | L2719-L2772 |
| schema 命令 | `cmd_schema()` | L2775-L2819 |
| format 命令 | `cmd_format()` | L2822-L2860 |
| lint 命令 | `cmd_lint()` | L2863-L2948 |
| 命令注册 | `main()` 中 | L2995-L3029 |

---


# 0004. 端侧记忆存储：LittleFS + 独立 storage 分区 + cJSON，文件经 POSIX 抽象（host 可测）

- 状态：被 0005 取代（Superseded by 0005，2026-06-12）
- 日期：2026-06-11
- 相关：ADR `0003`（端侧自治）、计划 `docs/design/plans/2026-06-11-on-device-autonomy.md`（P4 §1）、规范 `docs/design/specs/on-device-memory-port-contract.md`（§1 目录布局、§9 markdown）、`firmware/partitions.csv`、`firmware/main/idf_component.yml`

## 背景

P4 要把记忆存储搬到设备 flash：画像 `profiles/<u>.json`、对话 `conversations/<u>/turns.jsonl`+`meta.json`、结构化记忆 `memories/<u>/index.json`+`<cat>/<id>_<title>.md`、长期摘要 `summaries/<u>/index.json`+`*.md`（规范 §1）。需要选定**文件系统**、**JSON 库**、**分区**，并尽量让存储逻辑能在 **host 上对 golden 验证**（沿用"先 web/host 验证再真机"）。

现状约束：
- `firmware/partitions.csv` 当前 4MB 布局只有 `nvs`(24K) + `factory`(app, ~4MB)，**没有数据分区**可挂文件系统；板子是 16MB flash（有富余）。
- `espressif__cjson` 已是 managed 依赖（`firmware/managed_components/espressif__cjson/`），源码可在 host 编译。
- ESP-IDF 自带 `spiffs` / `fatfs`；`LittleFS`（`joltwallet/littlefs`）为 registry 组件。

## 备选方案

**文件系统**
- **SPIFFS**：扁平命名空间（无真正目录）、空间接近满时性能骤降、无掉电一致性保证、官方已不推荐新设计。我们的布局是**深目录 + 多小文件 + turns.jsonl 频繁追加**，SPIFFS 不契合。
- **FATFS（+ wear levelling）**：有目录、生态成熟，但元数据开销大、对多小文件不经济、掉电一致性弱。
- **LittleFS（选定）**：为 MCU 设计，**目录支持 + 磨损均衡 + 掉电掉电安全（power-loss resilient）**，多小文件友好，注册为 VFS 后可用标准 POSIX `fopen/mkdir/...`。

**JSON 库**
- **cJSON（选定）**：已是依赖、纯 C 可移植（host 同样可编译）、足够解析/序列化我们的索引与画像。
- 自研 mini-parser：省依赖但易错，无必要。

**文件访问抽象**
- 直接用 POSIX stdio：LittleFS 在设备上挂成 VFS，`fopen/fread/fwrite/mkdir/opendir` 在**设备与 host 一致**；只有**挂载根路径**不同。
- 自定义 IO 回调层：更可控但更重，当前无需。

## 决策

1. **文件系统用 LittleFS**，新增独立 **`storage` 数据分区**承载记忆文件；扩 `partitions.csv` 到 16MB 布局（app 维持现状，新增数 MB 的 `storage`）。具体大小在实施时定，预留磨损均衡冗余。
2. **JSON 用 cJSON**（既有依赖）做索引/画像/meta/摘要索引的解析与序列化；`turns.jsonl` 按行 JSON 追加。
3. **文件访问走 POSIX**：存储模块只依赖 `fopen/fread/fwrite/mkdir/rename/remove/opendir`，通过一个**仅含挂载根前缀**的薄配置切换 host（临时目录）与设备（LittleFS 挂载点）。存储逻辑因此**可在 host 用临时目录 + cJSON 对 golden 验证**。
4. **本地文件不要求与服务端字节对齐**：双向同步走控制平面**内存快照**（`memory_sync`/`memory_report`，ADR 0003），不直接比对设备文件。故设备端 JSON 缩进/键序/空白**无需**复刻 Python `json.dumps`；只要求**有效、可往返、检索/Prompt 行为对齐 golden**（`.md` 渲染另由 `vr_markdown` 已对齐，规范 §9）。
5. **目录布局与字段语义**严格对齐规范 §1（两端同构），保证快照能在设备本地重建。

## 后果

- 正面：
  - 目录/小文件/追加场景用对了 FS；磨损均衡 + 掉电安全契合儿童设备频繁断电场景。
  - POSIX 抽象让**存储逻辑 host 可测**（cJSON 纯 C），延续 golden 驱动、真机后置。
  - 不追求文件字节对齐，避免在设备上复刻 Python JSON 格式化的无谓工程量。
- 负面 / 代价：
  - 需**改分区表**（加 `storage`），与烧录流程同步；首刷需擦写/格式化分区。
  - 引入 LittleFS 组件依赖；二进制体积与挂载初始化成本（可接受）。
  - host 与设备仅"挂载根路径"差异，但**真机 flash 行为（磨损、满盘、掉电）仍须真机验收**，不可用 host 临时目录冒充。
- 后续关注：
  - `storage` 分区容量与记忆增长（turns.jsonl 滚动、摘要累积）；写寿命与 `turns.jsonl` 追加频率。
  - 大缓冲的栈/堆放置（既有遗留项）：存储路径上的解析/渲染缓冲宜走堆或静态，避免任务栈溢出。
  - 是否给 `turns.jsonl` 加上限/滚动清理（端侧容量有限，服务端无此压力）。

# Music Cut

[![GitHub](https://img.shields.io/badge/GitHub-shuanmiangui%2Fmusic--cut-181717?logo=github)](https://github.com/shuanmiangui/music-cut)

一款**完全在本机运行**的五轨音乐分离工具，面向视频剪辑：把一首 MP3 / WAV 拆成
**人声、鼓、贝斯、钢琴、其他乐器**五条独立 WAV，直接拖进 Final Cut Pro、DaVinci Resolve
或 Premiere 使用。

![Music Cut 界面](assets/screenshot.png)

- 成熟开源模型：**Demucs v4 · HTDemucs 6s**（Meta / Alexandre Défossez，MIT）
- 本地计算，音频不上传
- 默认 **48 kHz / 24-bit PCM / 立体声**，也支持 44.1 kHz 与 32-bit float
- 针对“不过度失真”设计：不做降噪、EQ、压缩、单轨响度归一化，不做硬削波
- Apple Silicon GPU（MPS）加速；失败时自动回退 CPU

---

## 快速开始

先获取代码（也可以在 GitHub 页面选择 **Code → Download ZIP** 后解压）：

```bash
git clone https://github.com/shuanmiangui/music-cut.git
cd music-cut
```

### 图形界面（推荐）

1. 首次使用，双击 **`setup.command`**，等待依赖和模型安装/校验完成。
   - 已存在 `.venv` 和模型时会直接复用，不会重复下载。
2. 双击 **`Music Cut.app`**。
3. 选择 MP3 / WAV → 选择保存位置 → 选择质量 → **开始分离**。
4. 完成后点击“在访达中显示”。

> 如果 macOS 提示无法验证开发者：在 Finder 中右键 `Music Cut.app` → **打开**。
> 如果 `.app` 没有正常启动，双击 **`Music Cut.command`**（会保留终端窗口便于查看错误），
> 详细日志在 `logs/gui.log` 和 `logs/musiccut.log`。

### 命令行

```bash
cd ~/Downloads/music-cut   # 换成你的实际解压/克隆位置

# 默认：高音质、48 kHz、24-bit
./bin/musiccut 歌名.mp3

# 指定输出目录
./bin/musiccut 歌名.wav -o ~/Movies/项目/音频

# 最终交付：最慢、最高质量
./bin/musiccut 歌名.mp3 -q max

# 32-bit float + 单独吉他轨
./bin/musiccut 歌名.mp3 --bit-depth 32f --keep-guitar

# 多首一起处理
./bin/musiccut a.mp3 b.wav
```

命令行帮助：`./bin/musiccut --help`

---

## 五条分轨

输出目录为 `<输出根目录>/<歌名>_stems/`，若已存在则自动使用 `_2`、`_3`，不会覆盖旧结果。

| 文件 | 内容 | 说明 |
| --- | --- | --- |
| `01_vocals.wav` | 人声 | 主唱、和声；不含伴奏 |
| `02_drums.wav` | 鼓 | 整套鼓组与打击乐 |
| `03_bass.wav` | 贝斯 | 贝斯、低音合成器等低频声部 |
| `04_piano.wav` | 钢琴 | 见下方“钢琴轨说明” |
| `05_other.wav` | 其他乐器 | 吉他、弦乐、合成器、铜管等；默认已并入吉他 |

- 使用 `--keep-guitar`（GUI：勾选“保留单独吉他轨”）会输出第六条 `05_guitar.wav`，
  此时 `06_other.wav` 不再包含吉他。
- 同时输出 `separation.json`，记录源文件、模型版本、参数、每轨峰值/响度、采样率、
  位深、是否触发防削波增益等，便于复现。
- 所有分轨**从 0 秒开始、长度完全一致**，可按时间线直接对齐。

### 导出格式

| 选项 | 默认 | 用途 |
| --- | --- | --- |
| 采样率 | `48000 Hz` | 视频时间线通用；也可选 44100 |
| 位深 | `24-bit PCM` | 兼容性最好，专业剪辑通用 |
| 可选位深 | `32-bit float` | 最大动态余量；支持 32f 的软件可用 |
| 声道 | 立体声 | 单声道源会复制为双声道 |

24-bit 立体声每秒约占 **288 KB**，每轨每分钟约 **17 MB**；五轨 4 分钟的歌曲约 **340 MB**。
32-bit float 体积再大约 1/3。这是无损 WAV 的正常体积。

---

## 音质设计

### 模型

- 模型：**HTDemucs 6-source**（`htdemucs_6s`），Demucs v4，官方 6 轨权重。
- 模型内部固定以 **44.1 kHz** 工作。输入若是 48 kHz，导入时会先高质量重采样到 44.1 kHz，
  导出时再以 **soxr VHQ** 重采样回目标采样率。
- 预训练权重放在 `models/demucs/`，启动时校验 SHA-256；不依赖 Hugging Face 在线加载。
- 权重文件（约 55 MB）不提交到 Git，`setup.command` 会自动下载并校验。

### 质量预设

| 预设 | shifts | overlap | 何时用 |
| --- | --- | --- | --- |
| `fast` 快速 | 1 | 0.25 | 试听、粗剪、批量筛选 |
| `high` 高音质（默认） | 2 | 0.50 | 大多数视频剪辑 |
| `max` 极致 | 4 | 0.75 | 最终交付前，耗时约翻倍 |

`shifts` 是测试时随机偏移平均，能减少分段伪影；`overlap` 是分段重叠。默认 `high`
在质量和耗时之间更实用；`max` 更慢，但不会神奇地修复模型本身识别不出的声部。

### 为什么不容易“过度失真”

1. **不裁剪**：24-bit 输出前统计所有分轨的峰值；若有任何一轨超过 -0.3 dBFS，
   对**所有分轨施加同一个增益**。这样不会削波，也不会破坏五条轨之间的相对音量。
   （单独压缩或单独归一化会破坏比例，所以不做。）
2. **32-bit float**：浮点输出保留超过 0 dBFS 的样本余量，不再施加防削波增益。
3. **不做破坏性后处理**：没有降噪、EQ、压缩器、限幅器或“智能美化”；这些会改变音色。
4. **高精度重采样**：只做分离必需的重采样，使用 soxr VHQ。
5. **无损导出**：WAV 不做有损编码。注意“无损 WAV”指文件编码无损，不代表 MP3 源能变成
   比原始录音更高的音质。

### 关于“无损”和重建

五条分轨保留同一增益，相加可近似还原原曲（存在模型残差，不能保证采样级完全抵消）。
如果要在剪辑中重新组合，**不要对单条轨单独做响度归一化**，否则比例会变。

---

## 钢琴轨说明（重要）

Demucs 官方的 6-source 模型是实验性的。官方文档也明确说明：**piano 是其中较弱的一轨**，
复杂编曲可能有串音或残留；这是模型能力边界，不是导出设置造成的。

我们在一个 30 秒“真实钢琴 + 合成鼓 + 合成贝斯”测试混音上测得钢琴轨 SI-SDR 约 **18.2 dB**，
说明在钢琴清晰的段落效果可用；但密集流行/摇滚编曲中不保证同样干净。建议：
- 把钢琴轨当作**辅助层**，在钢琴独奏段落提高音量，在齐奏段落适当降低或配合音量包络；
- 先试听，再决定是否使用钢琴轨；不要仅凭文件名假定它绝对干净。

目前同时提供“人声/鼓/贝斯/钢琴/其他”的成熟开源本地模型，仍以 Demucs 6s 最实用。

---

## 在视频剪辑软件中使用

1. 新建 48 kHz 时间线（或使用与素材一致的采样率）。
2. 把五条 WAV **一起从 0 秒拖入**；它们的长度和起点一致，不需要手动对轨。
3. 需要突出某声部时 solo 对应轨道；需要伴奏时静音人声轨。
4. 保留 24-bit 工程；如果 NLE 支持 32-bit float，可优先用 32f。
5. 若要把分轨重新混合成完整伴奏，保持各轨推子为 0 dB，不要在单轨上做归一化。

---

## 安装、环境与文件

要求：**Apple Silicon macOS 14+**，建议 8 GB 以上内存，至少 3 GB 可用磁盘空间。
本项目在 macOS 15.7 / Apple M4 / 24 GB 上验证。

- `Music Cut.app`：原生 arm64 启动器 + Tk 图形界面，双击即用。
- `Music Cut.command`：备用启动方式，会打开终端显示错误。
- `setup.command`：用 [uv](https://docs.astral.sh/uv/) 创建本目录内的 `.venv`、安装固定版本依赖、
  校验/下载模型。未安装 uv 时会尝试把 uv 下载到 `.tools/`。
- `bin/musiccut`：命令行入口。
- `models/demucs/`：模型 bag 配置随仓库提供；约 55 MB 的 `.th` 权重**不入 Git**，
  首次 setup 时从 Meta 的 CDN 下载并按 SHA-256 校验。
- `output/`：默认输出根目录。
- `logs/`：运行日志。

首次安装需要网络下载 Python 依赖和约 55 MB 模型；之后可完全离线运行。
`.app` 使用本机 ad-hoc 签名；如果复制到另一台 Mac 后被 Gatekeeper 拦截，请右键 → 打开，
或使用 `Music Cut.command`。

---

## 性能参考

在 M4 / MPS 上，20 秒测试片段使用 `fast` 的完整流程约 5 秒；包含模型加载、分段推理、
重采样和写盘。`high` 约为 `fast` 的 1.5–2 倍时间，`max` 再翻倍左右。实际速度取决于
歌曲长度、编曲密度和内存压力；长曲建议先用 `fast` 试听。

---

## 故障排查

| 现象 | 处理 |
| --- | --- |
| 双击 `.app` 没反应 | 用右键 → 打开；或运行 `Music Cut.command` 查看终端错误 |
| GPU / MPS 报错 | 程序会自动回退 CPU；CLI 可显式加 `--device cpu` |
| 提示模型下载失败 | 检查网络；模型文件应位于 `models/demucs/`，也可重新运行 `setup.command` |
| 输出目录权限错误 | 在 GUI 里点“更改…”换到用户目录，如 `~/Movies/项目/音频` |
| 分离时间太长 | 先用 `fast`；缩短测试片段；关闭其他占内存的软件 |
| 钢琴轨有串音 | 这是 6s 模型的已知限制；用音量包络处理，或只取钢琴清晰的段落 |
| 想减少体积 | 选 44.1 kHz 和 24-bit；不要为了省空间改成 MP3，那会引入有损压缩 |

---

## 测试

```bash
# 快速单元测试，不加载模型
.venv/bin/python -m unittest discover -s tests -v

# 完整端到端测试（会加载模型，约几秒到十几秒）
MUSICCUT_RUN_INTEGRATION=1 .venv/bin/python -m unittest tests.test_integration -v
```

---

## 许可与致谢

- 本项目代码：MIT，见 [LICENSE](LICENSE)。
- 分离模型：[Demucs v4 / HTDemucs](https://github.com/adefossez/demucs)，Meta 与
  Alexandre Défossez，MIT。权重文件来源与校验值见 [models/README.md](models/README.md)。
- 音频版权归原作者所有；请只处理你有权使用的音乐。

Music Cut 不是“一键完美分轨”神话：它是一个把当前成熟开源模型、可靠的音频工程处理和
适合剪辑的交付格式组合起来的本地工具。先试听，再决定每条轨在项目里的用法。

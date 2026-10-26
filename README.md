# HM Hub Native

HM Hub 的 **320×170 RGB565 实时显示固件扩展**，配套 Python 播放器、构建工具和离线回归测试。图像逐条带写入 LCD，复用 6,400 字节 RAM，无需整帧缓冲，不写相册 Flash。

这是接入官方 **1.3.7** 二进制的扩展源码，适配 USB `c019:0401`、硬件 ID `0xa101`、Cortex-M4。板级地址仅适用于下述固定镜像；仓库不含官方完整固件源码或预编译镜像。

## 值得复用的实现

| 代码 | 实现 |
| --- | --- |
| [`firmware/stream_receiver.c`](firmware/stream_receiver.c) | HMS1 状态机、包/帧 CRC、顺序与边界检查、显示租约、两个 5 行缓冲交替 DMA |
| [`firmware/cdc_stream_rx.c`](firmware/cdc_stream_rx.c) | 无动态分配的 USB 分片组包、固定长度队列和容量背压 |
| [`firmware/board_patch.c`](firmware/board_patch.c) | USB HS 512 / FS 64、主循环调度、LCD 所有权、非阻塞 TX、看门狗恢复 |
| [`firmware/patch_entry.S`](firmware/patch_entry.S)、[`board_patch.ld`](firmware/board_patch.ld) | Thumb 跳板、Flash/RAM 边界和零初始化区接入 |
| [`tools/build_patch.py`](tools/build_patch.py) | 校验基础镜像、交叉编译、生成跳转补丁及逐项修改清单 |
| [`host/native_stream.py`](host/native_stream.py) | 能力协商、窗口 ACK、ffmpeg 解码、实时播放及模拟器接口 |
| [`tests/`](tests/) | C 接收器测试、真实 Thumb 机器码仿真、延迟 DMA、USB 和读回背压回归 |

移植接收器时，实现 [`stream_receiver.h`](firmware/stream_receiver.h) 中的板级回调即可；不要直接复用本板的固定地址。独立构建保留自己的包 CRC，只有经过原固件 CRC 校验的调度入口才可启用快速路径。

## 构建与检查

需要 Python 3.10+、C 编译器、`make`、Clang 和 `ld.lld`。播放器另需 ffmpeg；建议使用 Linux。Ubuntu/Debian 示例：

```sh
sudo apt-get install build-essential clang lld python3-venv ffmpeg
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
make test       # 独立 C 接收器、传输和主机工具测试，不需要官方镜像
make prepare    # 从官网获取固定版本，验证 JS 和提取后镜像的 SHA-256
make check      # 构建固件并执行全部离线检查，不访问 USB
```

官网输入为 [`app_bin_data.js`](https://hmlab.tech/hub/1.3.7/app_bin_data.js)。基础镜像 SHA-256：

```text
5d6c874d1d37a5c20d5a4404502c81e93f4dbca261862b56c3f8041535536f77
```

离线可用 `python tools/extract_firmware.py --source /path/to/app_bin_data.js`。输入写入忽略的 `vendor/`，构建与测试结果写入 `build/`；官方输入变化时会拒绝构建。`make firmware` 的默认产物为 `build/native-bulk512-fast-pipeline-compatible-release.bin`，修改清单为 `build/candidate-review.json`。

## 播放与刷写

设备已安装扩展时：

```sh
python host/native_stream.py --probe-only
python host/native_pattern.py --seconds 15
python host/native_stream.py /path/to/video.mp4 --fps 12 --realtime --loop
```

默认按 USB VID/PID 自动选择唯一设备；连接多台时加 `--port /dev/ttyACM0`。可以把 `--port` 指定为 Windows 的 `COM3` 等串口名。STOP 或超过 1 秒没有有效帧数据会恢复原页面。仿真播放可加 `--simulate`。

默认构建保留官方版本号 `0x10307`，以兼容官方 1.3.7 网页；是否支持实时显示通过 HMS1 HELLO 判断。默认刷写命令只校验基础镜像；实际写入必须显式选择操作：

```sh
python host/dfu_update.py                         # 仅本地校验
python host/dfu_update.py --write-native-compatible-release
python host/dfu_update.py --write-original        # 回退到固定的官方 1.3.7
```

刷写器默认只接受已验证的扩展哈希 `162404c55af2ab737407c4532d483de1a397cd802ac4e74cd475a80b13902678`。编译器版本可能改变产物；评审本地 `candidate-review.json` 并通过 `make check` 后，可用 `--image-sha256 <完整SHA-256>` 明确指定本次构建。哈希用于固定所选择的文件，并不证明新构建已通过实机验证。扩展写入后必须完成双向能力握手，不能仅靠版本号判断成功。

## 实现边界

仅支持方向 0。条带逐步刷新，没有 TE 同步或原子换帧，可能撕裂；END ACK 表示 CRC 正确且最后一次 DMA 完成。离线测试不能证明实体画面或实机最高帧率。刷写会改写 MCU 应用区；断电或坏固件冷启动后的恢复未经完整验证。

详细接口见 [协议](docs/PROTOCOL.md) 和 [板级接入](docs/BOARD_INTEGRATION.md)。源码以 **AGPL-3.0-or-later** 发布，见 [LICENSE](LICENSE)；来源说明见 [NOTICE](NOTICE)。官方镜像的权利归原权利人，不在此源码许可证授权范围内。

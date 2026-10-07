# Gazebo / RViz 在 WSL 下的排障指南

> **什么时候用这份文档：** 图形界面（Gazebo、RViz）打不开、黑屏、卡住、闪退、卡顿时。
>
> **用法：** 按"症状"找到对应小节，照着做。每条都给了解释，你可以理解后再动手。

---

## 第 0 步：先确认 WSLg 本身是否正常

**这一步能在 10 秒内区分「WSLg 坏了」和「只是 Gazebo 的问题」。**

```bash
sudo apt install -y x11-apps
xeyes
```

| 结果 | 结论 | 下一步 |
|---|---|---|
| 弹出一个跟着鼠标转的眼睛窗口 | ✅ WSLg 正常 | 问题在 Gazebo，看第一～三节 |
| 报 `Error: Can't open display` 或没反应 | ❌ WSLg 有问题 | 看第五节 |

按 `Ctrl+C` 关掉 xeyes。

---

## ★ 实测已确认的两个问题（本项目真实踩到过，已解决）

> 下面两条是在 Intel Arc 核显 + WSL2 + WSLg 环境下**实际发生并已修复**的问题。
> 现象很有迷惑性，特此记录。

### 问题一：机器人一生成，gz sim 就段错误崩溃

**症状：**

终端里看到机器人生成成功了：

```
[spawn_entity.py-3] [INFO] ... Spawn status: SpawnEntity: Successfully spawned entity [<机器人>]
```

**紧接着 Gazebo 服务端（gz sim）崩溃：**

```
[ERROR] [gz sim-1]: process has died [pid 925, exit code -11]
```

`exit code -11` 就是 **SIGSEGV（段错误）**。

**后果：** Gazebo 一死，`/scan`、`/odom` 全部消失。现象是
"Gazebo 好像启动了，但什么话题都收不到"——**极易误判成插件没装或网络问题**。

**原因：** 机器人模型里的**相机传感器**需要 OpenGL 渲染上下文，
WSLg 的 D3D12 路径下会崩溃。

**解决办法（二选一）：**

1. **先用不带相机的机器人模型**（做导航开发够用，激光雷达才是关键）
2. **启用软件渲染**（见问题二），软件渲染下相机往往也能正常工作

> ⚠️ **仿真组注意：** 如果你们做的机器人模型里带相机，
> 可能会踩同一个坑。先用软件渲染确认一下。

### 问题二：RViz 打开后立刻闪退

**症状：**

```
[rviz2-3] [ERROR] [rviz2]: Vertex Program:rviz/glsl120/indexed_8bit_image.vert
          Fragment Program:rviz/glsl120/indexed_8bit_image.frag GLSL link result :
[ERROR] [rviz2-3]: process has died [pid 1417, exit code -11, cmd '.../rviz2 ...']
```

关键线索是 **`GLSL link result`** —— RViz 的着色器链接失败，同样是段错误。

**原因：** WSLg 的 D3D12 驱动对 RViz 使用的传统 GLSL 着色器（glsl120）兼容性不好。

**解决办法（已验证有效）：**

```bash
echo 'export LIBGL_ALWAYS_SOFTWARE=1' >> ~/.bashrc
source ~/.bashrc
```

让 Mesa 改用 **llvmpipe 软件渲染**（CPU 画图）。

**代价与收益：**
- 渲染变慢，但 RViz 的画面本来就不重（激光点、地图、路径），**做开发完全够用**
- **优点：几乎必定能开**，不再受驱动兼容性影响
- 录视频时 RViz 用软件渲染也没问题

**如果还想保留硬件加速，可以先试：**

```bash
export MESA_GL_VERSION_OVERRIDE=3.3
export MESA_GLSL_VERSION_OVERRIDE=330
```

（本项目实测这条对 RViz 无效，最终还是用了软件渲染。）

---

## 一、Gazebo 启动后卡住不动（★ 国内最常见）

**症状：**
终端里跑 `ros2 launch community_nav sim.launch.py` 后，
日志停在类似
`[gazebo-1] [INFO] [xxx]: Loading world...`
的位置，**几分钟都没有动静**，窗口也不出现。

**原因：**
Gazebo（Harmonic）启动时会尝试联网访问 Fuel 模型库 `fuel.gazebosim.org`
去下载模型。**这个域名在国内访问极慢或直接超时**，Gazebo 就卡在那里等。

**处理：**

```bash
# 1) 清掉可能已经损坏的 Fuel 缓存
rm -rf ~/.gz/fuel

# 2) 把 Fuel 模型库设为离线模式（Harmonic 用 Fuel，不是 Classic 的 models.gazebosim.org）
mkdir -p ~/.gz/fuel
printf 'url: ""\n' > ~/.gz/fuel/config.yaml
```

然后重新启动。

> **为什么会这样（答辩素材）：** Gazebo Harmonic 默认配置了 Fuel 在线模型库，
> 启动时会尝试同步模型索引。在无外网或网络受限环境下会造成长时间阻塞。
> 我们的 world 与全部模型都已内联/本地化，本就不需要联网下载；
> 若仍卡住，把 Fuel 的 `url` 置空即可改为完全使用本地模型文件，
> 这也让整个仿真环境**离线可用、结果可复现**。

---

## 二、Gazebo 窗口弹出但全黑 / 花屏 / 一闪就退

**原因：**
WSLg 下的 OpenGL 版本协商问题。Gazebo Harmonic 用的 OGRE2 渲染引擎
**要求 OpenGL 3.3 以上**，而 WSLg 默认上报的版本有时不满足。

**处理（按顺序试，一个不行再试下一个）：**

**① 覆盖 OpenGL 版本号（首选，保留硬件加速）**

```bash
echo 'export MESA_GL_VERSION_OVERRIDE=3.3'  >> ~/.bashrc
echo 'export MESA_GLSL_VERSION_OVERRIDE=330' >> ~/.bashrc
source ~/.bashrc
```

**② 还不行 → 强制软件渲染（慢但几乎必定能开）**

```bash
echo 'export LIBGL_ALWAYS_SOFTWARE=1' >> ~/.bashrc
source ~/.bashrc
```

> 软件渲染帧率会明显下降，但我们的场地只有 4.2 m × 4.2 m、物料也不多，
> **做建图和录像完全够用**。先让它能跑起来，比追求流畅更重要。

**③ 想看真正的报错信息**

```bash
gz sim --verbose
```

Gazebo 详细日志会打印 OGRE2 / OpenGL 的具体错误，**把这段贴给我**。

---

## 三、Gazebo 能打开但非常卡

**按影响从大到小：**

1. **关掉 Windows 侧占用 GPU 的程序**（浏览器硬件加速、游戏、录屏软件）
2. **降低 Gazebo 画质**：在 Gazebo 窗口里 `Edit → 关闭 Shadows`；左侧 `Layers` 面板可以关掉不需要的图层渲染
3. **分离启动 server 与 GUI**：
   ```bash
   # 终端 1（无界面，只跑物理引擎 —— 建图和导航其实只需要这个）
   gz sim -s
   # 终端 2（只在需要看画面时才开）
   gz sim -g
   ```
   > 录制视频时再开 GUI，平时调试可以只开 `gz sim -s` + RViz，省一半资源。
4. **降低分辨率**：在 Windows 的 WSL 设置里降低窗口缩放，或把 Gazebo 窗口调小
5. **确认确实是 GPU 加速**：
   ```bash
   ls /dev/dxg && echo "GPU 直通可用"
   ```

---

## 四、中文显示成方块 / 乱码

**症状：** RViz 或 Gazebo 里，中文标签全部显示成方框 `□□□`。

**原因：** Ubuntu 容器里没装中文字体。

**处理：**

```bash
sudo apt install -y fonts-noto-cjk
```

**这条很重要** —— 你的答辩视频里如果 RViz 界面上的中文站点名显示成方块，
会明显影响"表达与展示能力"的评分。**现在就装上，别等录像时才发现。**

> 安装脚本 `install_ros2.py` 已经把 `fonts-noto-cjk` 包含在内了，正常安装不会遇到这个问题。

---

## 五、`Failed to open display` / 图形窗口完全打不开

**处理顺序：**

**① 在 Windows PowerShell 里更新 WSL**

```powershell
wsl --update
wsl --shutdown
```

然后重新打开 Ubuntu 终端。

**② 检查环境变量**

```bash
echo "DISPLAY=$DISPLAY"
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
```

正常情况下应该有值（例如 `DISPLAY=:0`、`WAYLAND_DISPLAY=wayland-0`）。
如果都是空的，说明 WSLg 没启动起来，重复步骤 ①。

**③ 确认 `/tmp/.X11-unix` 存在**

```bash
ls /tmp/.X11-unix
```

**④ 还不行 → 确认 Windows 版本**

WSLg 需要 **Windows 11**（或 Windows 10 版本 21H2 以上且手动安装）。
在 Windows PowerShell 里查：

```powershell
[System.Environment]::OSVersion.VersionString
```

---

## 六、RViz 特有问题

| 症状 | 处理 |
|---|---|
| 报 Qt platform plugin 错误 | `export QT_QPA_PLATFORM=xcb` 后重试 |
| 打开后一片灰、没有地图 | 确认 Gazebo 在跑、`use_sim_time` 为 true、已 `source` 环境 |
| 提示 `no tf data` | 机器人没起来，或 TF 链断了；用 `ros2 run tf2_tools view_frames` 生成 TF 树 PDF 检查 |
| 中文显示方块 | 见第四节，装 `fonts-noto-cjk` |

### 6.1 ★ RViz 打开了，但看不到激光点 / 看不到地图在生长

这是**建图阶段最常见、也最容易误判**的一类问题。
在动任何配置之前，先按下面顺序排查。

#### 第 1 步：看左下角的 `Fixed Frame`（最关键）

RViz 窗口**左下角**有一行 `Fixed Frame`。它是 RViz 的参考坐标系——
**所有显示项都要能变换到这个坐标系，变换不出来就什么都不画。**

| `Fixed Frame` 的状态 | 含义与处理 |
|---|---|
| 正常显示 `map`，无警告 | ✅ 坐标系没问题，看第 2 步 |
| **显示成红色/黄色，或旁边有警告图标** | ❌ **这个坐标系当前不存在** → 继续往下看 |

**一个非常有用的判别技巧：把 `Fixed Frame` 临时改成 `odom`。**

- 改完之后**激光点出现了** → 说明 `/scan` 数据是好的，
  问题在于 **`map` 坐标系还没被发布**（也就是 SLAM 还没建立起 `map→odom` 变换）
- 改完**还是什么都没有** → 说明 `/scan` 本身就没数据，去看第 3 步

> **为什么 `map` 可能不存在：** `map` 坐标系由 slam_toolbox 发布。
> 如果它没被生命周期管理器激活（配置阶段失败），`map→odom` 就不会出现，
> RViz 以 `map` 为参考系时自然什么都画不出来。
>
> 手动确认一下：
> ```bash
> ros2 lifecycle get /slam_toolbox
> ```
> 应该输出 `active`。如果是 `unconfigured` / `inactive`，说明它没起来。

#### 第 2 步：检查 Displays 面板里两项是否正常

RViz 左侧 `Displays` 面板里应该能看到这些项：

| 显示项 | Topic 应为 | 状态应为什么 |
|---|---|---|
| `LaserScan` | `/scan` | Enabled ✅ |
| `Map` | `/map` | Enabled ✅ |
| `RobotModel` | （读 `/robot_description`） | Enabled ✅ |

**如果某一项前面有黄色 ⚠ 或红色 ✖ 图标**，把鼠标停上去看提示——
那通常就是答案（比如 `no transform from [base_scan] to [map]`）。

**如果面板里根本没有 `LaserScan` 这一项**，点左下角 `Add` 按钮手动加一个，
Topic 选 `/scan`。

#### 第 3 步：确认数据本身在发

```bash
ros2 topic hz /scan
ros2 topic hz /map
```

| 结果 | 结论 |
|---|---|
| `/scan` 有频率（5~10） | 雷达正常 |
| `/scan` 一直没输出 | 仿真侧的问题，看第一节 |
| `/map` 有频率（1 左右） | SLAM 在建图 |
| `/map` 一直没输出 | slam_toolbox 没工作，见上面 `ros2 lifecycle get` |

#### 第 4 步：视野可能只是太近了

**把鼠标放在 RViz 窗口里滚动滚轮往后退**（缩小视野）。

RViz 默认视角是**放大**的，很多时候地图其实在长，只是你看的是车旁边一小块。

> **如果嫌一项项排查麻烦**，直接跑诊断脚本，它会把整条链路一次看全：
> ```bash
> python3 "/mnt/d/project（ai/docs/scripts/diagnose_slam.py"
> ```

### 6.2 ★ 车"在动"但激光读数完全不变？

**这不是 RViz 的问题，是机器人根本没动。**

关键：**Gazebo 的 `/odom` 是开环的**——由轮速积分得来，**不测量实际位移**。
所以"轮子在转、`/odom` 在变"**不能证明机器人真的动了**。
真正能证明移动的是**激光读数发生改变**。

常见原因是**出生点在障碍物内部**，轮子空转。换一个空闲出生点：

```bash
ros2 launch community_nav sim.launch.py gui:=false x_pose:=-2.0 y_pose:=-0.5
```

（`-2.0 / -0.5` 是 TurtleBot3 官方世界的默认空闲位置。）

### 6.3 ★★ `/map` 一直不出来 —— slam_toolbox 没工作

> ### ⚠️ 先看这一段：这个问题的答案【随 ROS 2 版本而变】
>
> 本项目从 **Humble** 迁移到 **Jazzy** 时，同一个现象的原因**完全相反**：
>
> | | ROS 2 **Humble** | ROS 2 **Jazzy** |
> |---|---|---|
> | `async_slam_toolbox_node` | **不是**生命周期节点 | **是**生命周期节点 |
> | 有没有 `get_state`/`change_state` 服务 | **没有** | **有** |
> | 生命周期管理器 | **多余**：会永远卡在<br>`Waiting for service slam_toolbox/get_state...` | **必需**：不加就完全不发 `/map`，**而且不报错** |
>
> **判断方法只有一条**（两个版本都适用）：
>
> ```bash
> ros2 service list | grep -E "get_state|change_state"
> ```
>
> - **列出来了** → 它是生命周期节点 → **需要**生命周期管理器
> - **什么都没列出来** → 它不是 → **不要**加管理器，加了只会刷屏
>
> **【教训】同一个组件在不同大版本之间可能有【行为性】差异，
> 不只是参数名不同。参数名写错至少会报错，行为差异是【静默】的。
> 迁移之后所有运行时行为都要重新验证。**
>
> ---
>
> ### ⚠️ 另一个 Jazzy 专属的坑：bond 心跳
>
> 加上生命周期管理器后，日志里可能出现：
>
> ```
> [lifecycle_manager_slam] Server slam_toolbox was unable to be reached
>                          after 4.00s by bond. This server may be misconfigured.
> [lifecycle_manager_slam] Failed to bring up all requested nodes. Aborting bringup.
> ```
>
> **节点其实已经激活、`/map` 也在正常发布**，但日志报得像失败了，会把人引到错误方向。
>
> 原因：Nav2 的生命周期管理器要求节点通过 **bond 心跳**持续证明自己活着，
> 这套机制由 `nav2_util::LifecycleNode` 提供，而 **slam_toolbox 用的是自己的
> 生命周期实现，不支持 bond**。
>
> **解决：把 bond 检查关掉。**
>
> ```yaml
> lifecycle_manager_slam:
>   ros__parameters:
>     node_names: ['slam_toolbox']
>     bond_timeout: 0.0        # ← 0 表示不检查心跳
> ```
>
> ---

**症状：** `/scan` 有数据、车也能动，但 `ros2 topic hz /map` 一直没有输出。

**这一节就是照着这个现象写的，按顺序走，不要跳步。**

#### 第 1 步：确认它真的没有（别被 QoS 骗了）

```bash
ros2 topic hz /map
ros2 topic list | grep map
```

> ⚠️ **一定要用 `ros2 topic hz` 来确认**，它的 QoS 是默认的 VOLATILE，
> 对任何发布方式都兼容。
> 如果有别的工具（或自己写的脚本）报"收不到 /map"，
> **先怀疑那个工具的 QoS**——`TRANSIENT_LOCAL` 的订阅者是收不到
> VOLATILE 发布者的消息的，会产生假阴性。

#### 第 2 步：看节点和它的生命周期状态

```bash
ros2 node list
ros2 lifecycle get /slam_toolbox
```

| 结果 | 说明 | 跳到 |
|---|---|---|
| **没有 `/slam_toolbox`** | 节点根本没起来 | 第 4 步 |
| 有，状态 `unconfigured` / `inactive` | 生命周期管理器没激活它 | 第 3 步 |
| 有，状态 `active` | 状态正常却不发地图 | 第 4 步 |

#### 第 3 步：手动激活（绕过生命周期管理器）

```bash
ros2 lifecycle set /slam_toolbox configure
ros2 lifecycle set /slam_toolbox activate
```

然后**再看一眼 `ros2 topic hz /map`**。

- **出现地图了** → 问题在生命周期管理器，不在 slam_toolbox 本身
- **`configure` 就报错** → 看报错内容，通常是参数问题（跳到第 5 步）

#### 第 4 步：隔离测试 —— 用官方启动文件

**这一步能把"我们的 launch/参数有问题"和"环境有问题"彻底切开。**

```bash
pkill -f slam_toolbox ; pkill -f lifecycle_manager

ros2 launch slam_toolbox online_async_launch.py use_sim_time:=true
```

等 10 秒，另开终端：

```bash
ros2 topic list | grep map
```

| 结果 | 结论 |
|---|---|
| **有 `/map`** | slam_toolbox 本身没问题 → **问题在我们的 `mapping.launch.py` 或参数文件** |
| **还是没有** | slam_toolbox 在这个环境里跑不起来 → 看第 5 步的原始日志 |

#### 第 5 步：手动跑节点，看它的原始输出

**绕过所有 launch 包装，直接看节点自己说什么。**

终端 A：

```bash
ros2 run slam_toolbox async_slam_toolbox_node --ros-args \
  --params-file $HOME/smart_community_ws/src/community_nav/config/slam_toolbox_params.yaml \
  -p use_sim_time:=true -r __node:=slam_toolbox
```

终端 B：

```bash
ros2 lifecycle set /slam_toolbox configure
ros2 lifecycle set /slam_toolbox activate
ros2 topic hz /map
```

**终端 A 里的输出就是要找的答案** —— 参数类型错误、文件读取失败、
依赖缺失都会在这里直接写出来。

#### 第 6 步：抓日志（求助时用）

```bash
pkill -f slam_toolbox ; pkill -f lifecycle_manager ; pkill -f rviz2

timeout 30 ros2 launch community_nav mapping.launch.py > /tmp/map2.log 2>&1

grep -E "ERROR|Traceback|error|died|State|configure|activate|slam" /tmp/map2.log
```

**把 `grep` 的输出贴给导航组。**

---

## 七、诊断命令速查

需要向别人求助时，**先跑这三条并把输出一起发出去**：

```bash
# 1) 环境自检（会列出所有缺失项）
python3 "/mnt/d/project（ai/docs/scripts/check_env.py"

# 2) 图形与 GPU 状态
echo "DISPLAY=$DISPLAY  WAYLAND=$WAYLAND_DISPLAY"
ls -l /dev/dxg 2>/dev/null && echo "GPU 直通 OK" || echo "无 GPU 直通"

# 3) Gazebo 详细日志
gz sim --verbose 2>&1 | head -40
```

---

## 八、一键恢复：把图形相关配置全部重置

如果试乱了，想回到干净状态：

```bash
# 删掉我们加过的图形相关行
sed -i '/MESA_GL_VERSION_OVERRIDE/d'   ~/.bashrc
sed -i '/MESA_GLSL_VERSION_OVERRIDE/d' ~/.bashrc
sed -i '/LIBGL_ALWAYS_SOFTWARE/d'      ~/.bashrc
source ~/.bashrc

# 清 Gazebo（Harmonic）缓存与 Fuel 离线配置
rm -rf ~/.gz/fuel

# 重启 WSL（在 Windows PowerShell 里执行）
# wsl --shutdown
```

---

## 九、优先级建议（时间紧的时候这样做）

**不要一开始就追求 Gazebo 图形界面完美。**

| 阶段 | 需要图形界面吗 | 说明 |
|---|---|---|
| 建图 | 需要 RViz | **Gazebo 可以不开界面**，`gz sim -s` 无头运行即可 |
| 导航调试 | 需要 RViz | 同上 |
| **录制视频** | **必须** | 要求 Gazebo + RViz 同屏，这一步不能省 |

所以：**先把 `gz sim -s` + RViz 这条路跑通**（这条路最稳），
把 Gazebo 客户端的显示问题留到录像前再集中解决。

这样即使 Gazebo 界面一直有问题，你的**算法开发和建图进度也不会被阻塞**。

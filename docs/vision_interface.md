# 视觉组对接接口约定

本文是视觉组写识别节点的**唯一对接依据**。所有话题名、消息类型、字段语义如下，请严格按此实现；
有改动需求请先同步导航组（改话题名/字段会直接导致巡检卡住或漏触发）。

---

## 1. 视觉组需要订阅的话题（导航组/仿真组发出）

### 1.1 相机画面

| 话题 | 类型 | 帧名 |
|---|---|---|
| `/camera/image_raw` | `sensor_msgs/msg/Image` | `camera_link` |
| `/camera/camera_info` | `sensor_msgs/msg/CameraInfo` | `camera_link` |

相机是装在机器人车头朝前的**单目 RGB**，分辨率 640×480。做 3D 定位/测距时用 `/camera/camera_info` 的内参 + `camera_link` 的 TF。

### 1.2 识别触发 `/patrol/capture`

- 类型：`std_msgs/msg/String`
- 内容：**JSON 字符串**。导航组把机器人开到某个站点、停稳后发一次，视觉组收到后执行对应识别任务。

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `waypoint_id` | string | 站点 id（`outsider_n` / `zone_a` / `park_3` …） |
| `task` | string | 任务类型，见下表 |
| `zone` | string | 区域标记（`A` / `B` / `sidewalk_north`，仅 `crowd_count` 用） |
| `slot` | int | 车位号（仅 `plate_ocr` 用） |
| `target` | string | 具体目标（`person_f1` / `person_f2`，仅 `outsider_detect` 用；其余为空） |
| `stamp` | float | 触发时刻（Unix 秒） |

`task` 取值与视觉组应做的识别：

| task | 应识别 | 关键字段 |
|---|---|---|
| `outsider_detect` | 判断此人是否为社区人员/外来人员 | `target`（`person_f1` / `person_f2`） |
| `crowd_count` | 数出该区域人数 | `zone`（`A` / `B` / `sidewalk_north`） |
| `plate_ocr` | 识别车牌字符 | `slot`（`2` / `3`） |

### 1.3 巡检状态 `/patrol/status`

- 类型：`std_msgs/msg/String`
- 内容：JSON 字符串。用于播报/调试。结束时有一条 `{"event": "patrol_finished", ...}`，含成功/失败站点统计。

---

## 2. 视觉组需要发布的话题（导航组订阅）

### 2.1 红绿灯状态 `/traffic_light/state`

- 类型：`std_msgs/msg/String`
- 内容：**大写** 的 `RED` / `YELLOW` / `GREEN`，`data` 字段直接是这三者之一。
- 频率：建议 ≥ 5 Hz（或状态变化时发布），导航组在停止线前轮询该话题等绿灯。
- ⚠️ 必须是严格大写；发 `red` 或 `Green` 都匹配不上，会被当作"未知"一直等到超时。

### 2.2 识别结果 `/detection/result`

- 类型：`std_msgs/msg/String`
- 内容：任意字符串（建议 JSON，便于结构化统计）。
- 何时发：收到 `/patrol/capture` 并完成识别后，**发一次结果**。导航组收到后即认为该站识别完成、继续下一站；若 10 秒内没收到会跳过该站（不中断整体流程）。

---

## 3. 可选订阅（不强制，视觉组自便）

`/scan`（激光）、`/odom`、`/tf`、`/map`（栅格地图）、`/amcl_pose`（定位）都由导航/仿真提供，
视觉组可按需使用（例如用 `/amcl_pose` 知道车现在在哪、朝向哪）。

---

## 4. 联调顺序建议

1. 先只跑仿真（`ros2 launch community_nav sim.launch.py`），确认能收到 `/camera/image_raw`，并在画面里看到场景。
2. 单独测 `/traffic_light/state`：让视觉节点对着画面里的红绿灯发 `RED/GREEN`，用 `ros2 topic echo /traffic_light/state` 确认导航侧能收到。
3. 单独测 `/detection/result`：手动发一条 `ros2 topic pub /detection/result std_msgs/msg/String '{data: "test"}'`，看巡检节点日志是否打印 `[识别结果] test`。
4. 跑 `navigation.launch.py autostart_patrol:=true`，看整圈流程里每个 `capture` 站是否正确触发、结果是否正确回传。

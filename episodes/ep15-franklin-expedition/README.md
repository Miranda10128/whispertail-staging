# WhisperTail ep15 — Franklin Expedition 配图清单

主题：1845 年富兰克林北极探险队，129 人失踪。
来源：全部来自 Pexels（免费可商用），经 Pexels 图片 CDN 下载（`images.pexels.com`，`w=1600`，只定宽、保持原始比例，**未做任何裁切**）。
核实：每张均用 `file` + PIL 核实为原生竖版 JPEG，无横版改裁；逐张目视检查，无人脸、无假文字、无假 logo。

| # | 文件 | Pexels ID | 摄影师 | 实际尺寸 | 比例 | 主题 |
|---|------|-----------|--------|----------|------|------|
| 01 | 01-ice-floes.jpg | 26690645 | Tomáš Malík | 1600×2400 | 2:3 | 北极冰海 / 深色海面上的浮冰 |
| 02 | 02-ship-frozen-arctic.jpg | 33932109 | Jygen Dechavez | 1600×2133 | 3:4 | 三桅帆船困在冰雪海面 |
| 03 | 03-snowstorm-peaks.jpg | 19468468 | Christopher Politano | 1600×2133 | 3:4 | 暴风雪 / 雾中雪峰 |
| 04 | 04-arctic-rocky-shore.jpg | 2058732 | Tobias Bjørkli | 1600×1958 | 约 4:5 | 荒凉北极海岸 / 阴云下的黑岩海岸与雪山 |
| 05 | 05-compass-map.jpg | 8231152 | Ishaan Aggarwal | 1600×2134 | 3:4 | 罗盘压在古旧世界地图上 |
| 06 | 06-blue-glacier-ice.jpg | 18443381 | nazarena pozza | 1600×2138 | 3:4 | 蓝色冰川冰体 |
| 07 | 07-aurora-mountain.jpg | 8150290 | Erike Fusiki | 1600×2400 | 2:3 | 极光下的雪山 |

原页面（按行对应上表）：
- 01: https://www.pexels.com/photo/drifting-ice-floe-in-the-sea-26690645/
- 02: https://www.pexels.com/photo/majestic-ship-on-frozen-arctic-landscape-33932109/
- 03: https://www.pexels.com/photo/rocky-mountain-peaks-during-snowstorm-19468468/
- 04: https://www.pexels.com/photo/body-of-water-near-rocks-2058732/
- 05: https://www.pexels.com/photo/a-compass-on-a-map-8231152/
- 06: https://www.pexels.com/photo/view-of-an-iceberg-18443381/
- 07: https://www.pexels.com/photo/view-of-aurora-borealis-against-snow-covered-mountain-8150290/

## 备注与取舍（已逐张目视核验）

- 01：深蓝海面＋白色浮冰，对比强、上半部大面积留白，适合开篇压标题。无人物、无文字。
- 02：Pexels 上唯一找到的竖版“帆船在冰中”实拍，三桅帆船＋冰封海面＋大面积蓝天，留白足。**注意：船头漆有小字船名 “REMBRANDT VAN RIJN”（真实漆字，非 AI 假文字），渲染成 1080×1920 后字很小；** 如坚持画面零文字，备选为 6889134（冰封海面＋远处现代货轮剪影，船型不对、但无文字）。其他竖版帆船候选（18518259、19709693、7596802）均带更明显的船名／码头／现代设备，已弃用。
- 03：雾中雪峰、白茫茫一片，暴风雪氛围足，无人物、无文字。
- 04：原定“石堆／荒凉北极海岸”二选一。竖版石堆候选（32160478、39686783）实拍都是夏季高山湖景、绿树草地，与北极荒凉气氛不符，已弃用；改用本题材的另一半——阴云下的北极黑岩海岸（挪威 Troms），荒凉感最贴合，无人物、无文字。
- 05：罗盘压在古旧地图上。**罗盘盘面自带 N／E／S／W 方位字母与度数刻度（真实仪器的固有标记，非假文字）；** 地图上的地名在成片尺寸下基本不可读。
- 06：Pexels 上没有竖版的真正水下冰山照片（搜到的水下／半水下冰山全是横版），按“相近主题”改用竖版蓝色冰川冰体（阿根廷 Perito Moreno），冰蓝色裂缝、冷峻醒目，无人物、无文字。
- 07：第一版极光图（11241268）下方是农庄木屋＋电线杆，已弃用；改用极光柱＋雪山山脊＋星空，无建筑、无人物、无文字。

## 渲染提示

- 渲染流水线按 `fill` 模式（等比放大填满 1080×1920 再裁边），2:3／3:4／4:5 的竖版图都会被左右轻微裁切，与之前各支做法一致；01、02、07 上半部留白大，适合 96pt 标题压字与标题词闪回。

# 综合服务楼的临时停用代价比较

这里保存综合服务楼的临时停用代价比较工作所用的匿名合成记录。文件不包含分析结果或居民个人资料。

## 资料

`samples/` 中每个文件都是独立的 UTF-8 JSON 文档：`trips`、`rooms`、`service_duration`、`transfer`、`shared_assistance`、`candidates`、`scenarios`、`bookings`、`accepted_changes`、`sampling_frame`、`observations`、`followup`、`decisions`、`review_policy`。日期是当地日历日期。空值表示没有对应观测或状态资料，不是数值零。

window 为分钟半开区间。场地入口决定行走耗时类别，transfer 已给出场地间耗时。protected 行程使用陪同点。固定预约涉及所列事项和场地；accepted_changes 是服务台已确认可替代的预约。

## 运行

使用 Python 3.11，无需第三方依赖或服务。`python3 inspect_samples.py` 逐个读取资料并列出记录数；`python3 -m unittest discover -s tests -v` 检查关键原始记录间的关系。输出只描述原始资料，不是业务比较结果。

"""Frozen original synthetic recognition inputs; no private materials."""
from src.material_function_transport import NAME
ENDPOINT=__import__('os').environ.get('TJU_LLM_BASE_URL','https://ai.tju.edu.cn/api/v3').rstrip('/')+'/chat/completions'
TEXTS=[
 '请检查实验表格中的缺失项，核对后提交结果。',
 '请阅读课程章节，写一段讨论并提交作业。',
 '请填写申请表，核对姓名和日期后上传。',
 '请计算样本均值，检查公式并提交计算过程。',
 '请绘制实验结果图，标注坐标轴并提交图片。',
 '请修改报告摘要，复核引用格式并提交报告。',
 '请运行实验代码，保存输出并核对结果。',
 '请整理访谈提纲，检查问题顺序后提交。',
 '请比较两种滤波方法，写出差异并提交说明。',
 '请检查文件清单，补齐附件并上传材料。',
]
class Done(BaseException):pass

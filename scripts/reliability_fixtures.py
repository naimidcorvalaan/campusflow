"""Independently authored public synthetic cases, frozen before live evaluation.

Gold describes required operations, not exact model wording. No scenario ids
are consulted by production code. Background cases must not invent work.
"""
from scripts.fc_original_ten import gold_rows
from src.material_workload import RATES


def recognition_cases():
    cases=gold_rows()
    # Meaningful variations across subjects, recipients, source layout and scale.
    rows=[
        ('报名信息','请填写社团报名表的姓名、学院和联系方式，检查后交给负责人。','basic_field review submit'),
        ('借用申请','请填写仪器借用申请，并准备导师签字证明，扫描后上传申请系统。','basic_field attachment submit'),
        ('奖学金材料','请核对奖学金申请表的分数，补齐成绩证明，再提交学院。','review attachment submit'),
        ('离校表','请填写离校登记表，检查日期。这里只保存草稿，不提交。','basic_field review'),
        ('调查录入','请将纸质问卷的选择答案录入表格，并核对漏项。','basic_field review'),
        ('积分题','请解答这份微积分作业中的定积分题，复核步骤并上传答案。','problem review submit'),
        ('线代证明','请证明正交矩阵的逆等于其转置，并检查证明中的条件。','proof review'),
        ('概率计算','请计算样本方差和标准差，复核分母的取值。','calculation review'),
        ('物理习题','请完成两道力学计算题，画出受力图并写出计算过程。','problem|calculation chart'),
        ('数据汇总','请计算实验数据的均值，绘制折线图，将图上传课程平台。','calculation chart submit'),
        ('读书札记','请阅读课程论文，撰写读书札记，复核引用后提交。','reading_100_words short_text|long_text review submit'),
        ('阅读讨论','第一段是阅读背景。\n请阅读指定章节。\n再写出观点和一个讨论问题，交到课程平台。','reading_100_words short_text|long_text submit'),
        ('课程报告','请撰写课程调研报告，正文约3000字，检查参考文献后提交。','long_text review submit'),
        ('摘要修改','请修订中文摘要，使逻辑连贯，并校对术语。','short_text|long_text review'),
        ('提纲设计','请撰写访谈提纲和开放式问题，检查问题顺序后交给小组。','short_text|long_text review submit'),
        ('英文润色','请修改英文短文的语法和衔接，校对后上传最终稿。','short_text|long_text review submit'),
        ('程序运行','请运行数值实验代码，核对终端输出并写入本地文件。','code review'),
        ('代码调试','请调试排序程序，运行测试用例，将运行结果导出到本地目录。','code'),
        ('脚本实现','请编写清洗数据的Python脚本，运行后检查缺失值处理结果。','code review'),
        ('代码交付','请运行实验程序，检查结果后把源代码上传教学平台。','code review submit'),
        ('图表复核','请检查统计图的单位和图例，修改图表后提交。','chart review submit'),
        ('文献检索','请查找三篇相关文献，写出检索综述并列出引用。','research short_text|long_text'),
        ('附件核验','请核对证明材料清单，准备在读证明与签字页，上传系统。','review attachment submit'),
        ('评分反馈','请按评分表给小组展示打分，并填写简短反馈。','score short_text|long_text'),
        ('复合录入','请填写实验登记信息、计算误差、绘制图表，最后检查并提交。','basic_field calculation chart review submit'),
        ('跨段提交','请计算实验样本的均值。\n结果需核对两遍。\n完成后提交计算过程。','calculation review submit'),
        ('有截止','请撰写读书摘要，明天18:00前上传课程平台。','short_text|long_text submit'),
        ('明确工时','请用30分钟检查实验报告中的图表标注，然后提交报告。','review submit'),
        ('无截止','请填写校内活动报名信息，检查联系方式。截止日期尚未公布。','basic_field review'),
        ('本地笔记','请阅读指定章节并撰写笔记，笔记只存本地，不要求上传或提交。','reading_100_words short_text|long_text'),
        ('清单整理','请检查项目文件清单，将缺失的证明附件补齐后交付。','review attachment submit'),
        ('对比说明','请比较给定两种算法的输出，写出差异说明并提交。','short_text|long_text submit'),
        ('数据复核','请复核这份计算表的公式和单位，将核对结果保存到本地。','review'),
        ('任务手册','任务一：填写实验记录。任务二：计算误差。任务三：撰写讨论并上传报告。','basic_field calculation short_text|long_text submit'),
        ('量表填写','请按实际情况填写评分量表，复核后上传。','score|basic_field review submit'),
    ]
    for label,text,groups in rows:
        groups=[g.split('|') for g in groups.split()]
        # Positive groups remain independent gold; additional grounded formal
        # categories are allowed except unsupported local-only submission.
        allowed=set(RATES)
        if 'submit' not in sum(groups,[]):allowed.discard('submit')
        cases.append(dict(fixture=len(cases)+1,label=label,text=text,expected_is_estimatable=True,
            expected_feature_groups=groups,allowed_feature_kinds=sorted(allowed),scope_patterns=[],
            evidence_role='explicit source instructions',fixed_requirements='preserve source facts'))
    for label,text in [
        ('校园背景','天津大学有北洋园和卫津路两个校区。本段只介绍校园背景，没有指定需要完成的任务。'),
        ('概念背景','样本均值是样本数值的平均数。这是概念说明，并未布置计算或提交任务。'),
        ('待定事项','老师说后续会公布具体任务。目前没有任务内容，暂不知道需要做什么。'),
        ('天气说明','今天校园天气晴朗，空气清新。这是一段观察记录，无需处理。'),
        ('历史简介','这段文字介绍图书馆的建设历史，供背景参考，没有阅读作业或其他要求。'),
    ]:
        cases.append(dict(fixture=len(cases)+1,label=label,text=text,expected_is_estimatable=False,
            expected_feature_groups=[],allowed_feature_kinds=[],scope_patterns=[],
            evidence_role='no actionable assignment',fixed_requirements='do not invent tasks'))
    assert len(cases)==50 and len({c['text'] for c in cases})==50
    return cases


def extended_recognition_cases():
    """25 additional assignments in direct and cross-paragraph source layouts."""
    cases=recognition_cases()
    rows=[
        ('设备盘点','请核对实验室设备清单，填写缺失的资产编号，再提交盘点表。','review basic_field submit'),
        ('费用报销','请填写报销表，准备发票附件，复核总额后递交。','basic_field attachment review submit'),
        ('活动反馈','请为讲座的组织情况评分，并撰写一段反馈。','score short_text|long_text'),
        ('矩阵运算','请计算两个矩阵的乘积，并核对维度。','calculation review'),
        ('几何证明','请证明三角形中位线定理，并写出推导过程。','proof'),
        ('热学题目','请完成热学作业中的计算题，并提交解题过程。','problem|calculation submit'),
        ('分布可视化','请绘制样本直方图，标注单位并核对图例。','chart review'),
        ('课程论证','请撰写一篇课程论证短文，检查引用格式。','short_text|long_text review'),
        ('文献摘要','请阅读给定文献并写出摘要，提交给助教。','reading_100_words short_text|long_text submit'),
        ('报告修订','请修订实验报告的讨论段落，核对术语后上传。','short_text|long_text review submit'),
        ('代码测试','请运行单元测试，检查失败记录，将结果保存到本地文件。','code review'),
        ('数据导出','请执行分析脚本，把结果导出到本地目录，不上传。','code'),
        ('课程仓库','请调试课程项目代码，核对测试结果，再上传源代码。','code review submit'),
        ('问题清单','请撰写调研问题清单，检查是否存在重复问题。','short_text|long_text review'),
        ('成果附件','请准备竞赛成果的证明附件，核对签字后上传。','attachment review submit'),
        ('材料引用','请检查报告中的引用编号，修正引用格式并提交。','review submit'),
        ('信息检索','请检索相关课程资料，撰写一段文献综述。','research short_text|long_text'),
        ('实验日志','请填写实验日志，计算测量误差并复核结果。','basic_field calculation review'),
        ('报告图表','请绘制结果对比图，撰写图注并上传报告。','chart short_text|long_text submit'),
        ('数据清洗','请编写数据去重脚本，运行并核对记录数。','code review'),
        ('会议准备','请撰写组会汇报提纲，检查顺序后发给导师。','short_text|long_text review submit'),
        ('申请说明','请填写项目申请表，撰写申请理由并提交。','basic_field short_text|long_text submit'),
        ('问卷设计','请撰写问卷题目，复核选项是否重叠。','short_text|long_text review'),
        ('数值检查','请计算这组数据的中位数，核对排序，再交付结果表。','calculation review submit'),
        ('阅读批注','请阅读章节并撰写批注，将文件保存在本地即可。','reading_100_words short_text|long_text'),
    ]
    for layout in ('direct','notice'):
        for label,text,groups in rows:
            if layout=='notice':
                text='背景介绍：本材料用于课程小组的下一次讨论，活动时间尚未确定。\n本次独立准备事项如下。\n'+text+'\n参考资料：其他小组采用的方法只供背景了解，无需照做。'
            groups=[g.split('|') for g in groups.split()]
            allowed=set(RATES)
            if 'submit' not in sum(groups,[]):allowed.discard('submit')
            cases.append(dict(fixture=len(cases)+1,label=label+'-'+layout,text=text,expected_is_estimatable=True,
                expected_feature_groups=groups,allowed_feature_kinds=sorted(allowed),scope_patterns=[],
                evidence_role='explicit instruction; background is not evidence',fixed_requirements='preserve action boundary'))
    assert len(cases)==100 and len({c['text'] for c in cases})==100
    return cases

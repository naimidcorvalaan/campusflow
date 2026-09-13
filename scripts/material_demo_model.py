"""Finite synthetic notice responses, never imported by the production adapter."""
import json
from scripts.low_input_model import LowInputModel

NOTICES = {
    'a':'@全体同学 高数第三章第3—12题，第8题写完整过程，2026-09-11 22:00前交。',
    'b':'高数第三章作业补充：第8题写完整过程，2026-09-11 22:00前交。',
    'existing_old':'高数第三章作业，第3—12题，第8题写完整过程，2026-09-10 22:00前交。',
    'c':'本周高数第三章3—12题，2026-09-11 22:00前交。数据结构实验一，约需2小时，2026-09-14 18:00前交。2026-09-07 16:00–17:00在33教开班会。',
    'relative':'高数第三章作业，本周五22:00前交。',
    'dirty_a':'@所有人 第三章作业周五之前交哈，3-12，8题过程写全，还是学习通那个入口，别忘了',
    'dirty_b':'老王说数据结构实验一好像下周一之前交，具体要求群文件里有，我还没看',
    'dirty_c':'这周高数3-12周五交，数据结构实验下周一交，然后周四下午两点33教开班会，大概一个小时',
    'dirty_d':'今天课真的坐牢哈哈哈，对了老师最后说那个小论文国庆前交，主题自己选，3000字左右，具体格式之后发',
    'dirty_e':'高数作业补充一下，第8题必须写完整过程，截止还是周五晚上十点',
    'dirty_f':'明晚之前交',
    'dirty_none':'今天课真的坐牢哈哈哈，中午食堂排队也太长了',
}


def timing(text, day=None, clock=None, days=None, week=None, weekday=None):
    return dict(text=text,date=day,clock=clock,offset_days=days,week_offset=week,weekday=weekday)


def row(title, evidence, **values):
    result = dict(kind='task',title=title,scope='第三章第3—12题',completion='第8题写完整过程',
        evidence=evidence,deadline=None,start=None,end=None,location_text=None,campus_id=None,
        commitment_kind=None,minutes=None,duration_evidence=None,uncertainties=[],possible_task_ref=None)
    result.update(values)
    return result


def issue(field,status,message,evidence):
    return dict(field=field,status=status,message=message,evidence=evidence)


class MaterialDemoModel(LowInputModel):
    def __init__(self,story='a'):
        super().__init__('b')
        self.material_story = story

    def _response(self,system,user):
        if 'campusflow.material-text.v2' in system:
            data = json.loads(user.split('\n上次输出')[0])
            text = data['material']
            story = next((key for key,value in NOTICES.items() if value==text),self.material_story)
            deadline = timing('2026-09-11 22:00','2026-09-11','22:00')
            if story == 'existing_old':
                deadline = timing('2026-09-10 22:00','2026-09-10','22:00')
            items = [row('高数第三章作业',text,deadline=deadline)]
            if story == 'relative':
                items = [row('高数第三章作业',text,
                    deadline=timing('本周五22:00',clock='22:00',week=0,weekday=5))]
            if story in ('b','dirty_e') and data['existing_tasks']:
                items[0]['possible_task_ref'] = data['existing_tasks'][0]['task_ref']
            if story == 'c':
                items = [row('高数第三章作业',text.split('。')[0],deadline=deadline),
                    row('数据结构实验一',text.split('。')[1],scope='实验一',completion='提交实验报告',
                        minutes=120,duration_evidence='约需2小时',
                        deadline=timing('2026-09-14 18:00','2026-09-14','18:00')),
                    row('班会',text.split('。')[2],kind='fixed_commitment',scope='',completion='',
                        start=timing('2026-09-07 16:00','2026-09-07','16:00'),
                        end=timing('2026-09-07 16:00–17:00','2026-09-07','17:00'),
                        location_text='33教',commitment_kind='meeting')]
            if story == 'dirty_a':
                items = [row('高数第三章作业',text,scope='第3—12题',completion='第8题写完整过程',
                    deadline=timing('周五之前',week=0,weekday=5),
                    uncertainties=[issue('deadline','missing','周五没有具体日期和钟点，需要确认','周五之前')])]
            if story == 'dirty_b':
                items = [row('数据结构实验一',text,scope='实验一',completion='具体要求尚未查看',
                    deadline=timing('好像下周一之前',week=1,weekday=1),
                    uncertainties=[issue('deadline','uncertain','截止可能是下周一，需要确认','好像下周一之前'),
                        issue('completion','missing','具体要求还在群文件里，可以以后补充','具体要求群文件里有，我还没看')])]
            if story == 'dirty_c':
                parts = text.split('，')
                items = [row('高数作业',parts[0],scope='第3—12题',completion='完成并提交',
                    deadline=timing('这周高数3-12周五交',week=0,weekday=5),
                    uncertainties=[issue('deadline','missing','周五没有具体钟点，需要确认',parts[0])]),
                    row('数据结构实验',parts[1],scope='数据结构实验',completion='完成并提交',
                    deadline=timing('下周一交',week=1,weekday=1),
                    uncertainties=[issue('deadline','missing','下周一没有具体钟点，需要确认','下周一交')]),
                    row('班会','周四下午两点33教开班会，大概一个小时',kind='fixed_commitment',
                        scope='',completion='',start=timing('周四下午两点',clock='14:00',week=0,weekday=4),
                        end=None,minutes=60,duration_evidence='大概一个小时',location_text='33教',
                        commitment_kind='meeting')]
            if story == 'dirty_d':
                items = [row('小论文',text,scope='自选主题，约3000字',completion='格式尚未发布',
                    deadline=timing('国庆前'),uncertainties=[
                        issue('deadline','missing','“国庆前”没有可安全采用的具体日期和钟点','国庆前'),
                        issue('completion','missing','具体格式之后才发布，可以以后补充','具体格式之后发')])]
            if story == 'dirty_e':
                items = [row('高数第三章作业',text,scope='',completion='第8题写完整过程',
                    deadline=timing('周五晚上十点',clock='22:00',week=0,weekday=5),
                    uncertainties=[issue('deadline','missing','需要通知日期才能确定这个周五','周五晚上十点')],
                    possible_task_ref=(data['existing_tasks'][0]['task_ref'] if data['existing_tasks'] else None))]
            if story == 'dirty_f':
                items = [row('待提交事项',text,scope='材料未说明',completion='提交',
                    deadline=timing('明晚之前',days=1),uncertainties=[
                        issue('deadline','missing','“明晚”缺少通知日期和具体钟点','明晚之前'),
                        issue('identity','missing','材料没有说明要提交什么','明晚之前')])]
            if story == 'dirty_none':
                items = []
            return json.dumps(dict(schema_version='campusflow.material-text.v2',reference_date=None,
                reference_evidence=None,items=items),ensure_ascii=False),'material'
        if 'Feedback Interpreter' in system:
            raw,role = super()._response(system,user)
            value = json.loads(raw)
            value['intent_type'] = 'no_change'
            return json.dumps(value),role
        if 'p3.unified-feedback.v1' in system:
            return json.dumps(dict(schema_version='p3.unified-feedback.v1',
                task_updates=[dict(target_task_ref='day_task_001',new_task_title=None,
                    progress_delta_minutes=20,set_total_minutes=None,set_total_source=None,
                    lifecycle_action='none',is_splittable=None,minimum_slice_minutes=None)],
                commitment_updates=[],movement=dict(has_movement=False,origin_text=None,
                    destination_text=None,mode=None,depart_at=None,arrive_by=None),
                current_location=None,questions=[],reason=None)),'progress'
        return super()._response(system,user)

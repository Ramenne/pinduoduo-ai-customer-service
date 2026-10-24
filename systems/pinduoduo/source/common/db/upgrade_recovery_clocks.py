"""明确执行的MySQL恢复时钟修复；不改变通用加法迁移规则，不回删业务记录。"""
from sqlalchemy import Column, Double, inspect, select, text
from sqlalchemy.schema import CreateColumn

TARGETS = {
    'pdd_message_inbox': ('due_at',),
    'pdd_message_delivery': ('due_at', 'baseline_at'),
    'pdd_message_poll_state': ('since', 'checked_at'),
    'pdd_knowledge_curation': ('due_at',),
}


def upgrade(engine):
    if engine.dialect.name != 'mysql':
        raise ValueError('恢复时钟修复仅适用于MySQL，请勿对其他数据库执行')
    changed = []
    preparer = engine.dialect.identifier_preparer
    # 部署时先停消费者。DDL自动提交，逐列检查使中断后可以安全续跑。
    for table, names in TARGETS.items():
        if not inspect(engine).has_table(table):
            continue
        for item in inspect(engine).get_columns(table):
            if item['name'] not in names or str(item['type']).upper() != 'FLOAT':
                continue
            default = item.get('default')
            column = Column(item['name'], Double(), nullable=item['nullable'],
                server_default=text(str(default)) if default is not None else None,
                comment=item.get('comment'))
            spec = str(CreateColumn(column).compile(dialect=engine.dialect))
            with engine.begin() as connection:
                connection.execute(text('SET SESSION lock_wait_timeout=10'))
                if table == 'pdd_message_poll_state' and item['name'] == 'since':
                    # FLOAT存储已舍入，扩大128秒重叠；原唯一消息ID消重，不重发旧效果。
                    connection.execute(text('UPDATE pdd_message_poll_state SET since=GREATEST(0,since-128)'))
                connection.execute(text(f'ALTER TABLE {preparer.quote(table)} MODIFY COLUMN {spec}'))
            changed.append(f'{table}.{item["name"]}')
    return changed


def flag_failed_inputs(session):
    """让旧版误记完成、且没有外发账的输入重新可见；不授权补发。"""
    from common.models.message_delivery_models import MessageInbox, MessageDelivery
    from common.services import conversation_control
    rows = list(session.scalars(select(MessageInbox).where(
        MessageInbox.state == 'done', MessageInbox.result == 'auto_send_unconfirmed',
        ~select(MessageDelivery.id).where(MessageDelivery.inbox_id == MessageInbox.id).exists()
    ).with_for_update()))
    for row in rows:
        row.state = 'review'
        row.result = 'pre_send_failure_requires_review'
        conversation_control.change(session,row.shop_pk,row.customer_uid,'human',
            reason='先前回复未成功发送，请核对当前问题后接待')
    return [row.id for row in rows]

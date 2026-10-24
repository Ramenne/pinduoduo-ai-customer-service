"""沿用原OIN SendVerdict.cs 的单一判决语义；API接受不等于回读证实。"""
from enum import Enum


class SendVerdict(str, Enum):
    IN_FLIGHT = 'InFlight'
    DELIVERED = 'Delivered'
    MAY_HAVE_LEFT = 'MayHaveLeft'
    NEVER_LEFT = 'NeverLeft'


def from_state(state):
    if state == 'confirmed':
        return SendVerdict.DELIVERED
    if state == 'not_sent':
        return SendVerdict.NEVER_LEFT
    if state in ('dispatching','accepted'):
        return SendVerdict.IN_FLIGHT
    return SendVerdict.MAY_HAVE_LEFT  # 未登记的新状态不能自行获得重发权。


def accepted_or_confirmed(row):
    return row['state'] == 'accepted' or from_state(row['state']) == SendVerdict.DELIVERED

// AI协作只由按钮提交。中文选词、换行及组合键都不能成为隐式发送。
export function shouldSendWithEnter(event, view) {
  return view === 'customer' && event.key === 'Enter' && !event.shiftKey
    && !event.ctrlKey && !event.metaKey && !event.altKey
    && !event.isComposing && event.keyCode !== 229
}

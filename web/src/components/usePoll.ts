import { useEffect, useState } from "react";

// fetcher를 isDone이 참이 될 때까지 주기적으로 부른다. deps가 바뀌면 다시 시작한다.
// 다시 시작할 때 이전 값을 비우지 않는다 (새로고침 중에 화면이 깜빡이거나 하위 컴포넌트 상태가 사라지지 않게).
// 다른 대상을 불러오는 경우 받은 값이 지금 대상의 것인지는 호출하는 쪽에서 확인한다.
export function usePoll<T>(
  fetcher: (() => Promise<T>) | null,
  isDone: (value: T) => boolean,
  deps: unknown[],
  intervalMs = 800,
): T | null {
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    if (!fetcher) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const v = await fetcher();
        if (stopped) return;
        setValue(v);
        if (!isDone(v)) timer = setTimeout(tick, intervalMs);
      } catch {
        if (!stopped) timer = setTimeout(tick, intervalMs * 2);
      }
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return value;
}

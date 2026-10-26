import { useEffect, useState } from "react";

// 진행 중인 실행 id를 브라우저 탭(sessionStorage)에 기억한다. 다른 탭으로 갔다 와도 화면이 같은 실행에 다시 붙는다
// (탭을 옮기면 화면 컴포넌트가 사라졌다가 다시 만들어진다. 실행 자체는 서버 메모리에 그대로 있다).
// 서버를 다시 띄워 실행이 없어졌으면(check 실패) 기억을 지우고 처음 화면으로.
export function useStoredId(key: string, check: (id: string) => Promise<unknown>): [string | null, (id: string | null) => void] {
  const [id, setIdState] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  });
  const setId = (value: string | null) => {
    setIdState(value);
    try {
      if (value) sessionStorage.setItem(key, value);
      else sessionStorage.removeItem(key);
    } catch {
      // 저장소를 쓸 수 없으면 이 화면 동안만 기억한다
    }
  };
  useEffect(() => {
    if (id) check(id).catch(() => setId(null));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return [id, setId];
}

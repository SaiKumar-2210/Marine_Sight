import { useEffect, useRef } from 'react';

/** Subscribe to backend push events (/api/events): scan progress/completion and AIS cache refreshes. */
export function useServerEvents(onEvent) {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket;
    let retry;
    let closed = false;
    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(`${proto}://${window.location.host}/api/events`);
      socket.onmessage = e => {
        try { handler.current(JSON.parse(e.data)); } catch { /* ignore malformed */ }
      };
      socket.onclose = () => { if (!closed) retry = setTimeout(connect, 5000); };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); socket?.close(); };
  }, []);
}

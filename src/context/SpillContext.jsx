import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getScan, getScans, getSpill, getSpills, rescan } from '../services/marineApi';
import { useServerEvents } from '../hooks/useServerEvents';

const SpillContext = createContext(null);

function yesterdayUtc() {
  return new Date(Date.now() - 86400000).toISOString().slice(0, 10);
}

export function SpillProvider({ children }) {
  const [params, setParams] = useSearchParams();
  const [date, setDateState] = useState(params.get('date') || null);
  const [spills, setSpills] = useState([]);
  const [load, setLoad] = useState({ state: 'idle' });
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [showRejected, setShowRejected] = useState(false);
  const [scanIndex, setScanIndex] = useState([]);
  const request = useRef(0);

  // Default date: the most recent completed daily scan (falls back to yesterday UTC).
  useEffect(() => {
    getScans().then(r => {
      setScanIndex(r.scans || []);
      if (!date) setDateState(r.latestComplete || yesterdayUtc());
    }).catch(() => { if (!date) setDateState(yesterdayUtc()); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const fetchSpills = useCallback(async (d, withRejected) => {
    const token = ++request.current;
    try {
      const res = await getSpills(d, withRejected);
      if (token !== request.current) return;
      if (res.state === 'loading') {
        setLoad({ state: 'loading', job: res.job });
        return;
      }
      setSpills(res.spills || []);
      setLoad({ state: 'ready', scan: res.scan });
      setSelectedId(cur => (res.spills || []).some(s => s.id === cur) ? cur : (res.spills?.[0]?.id ?? null));
      getScans().then(r => setScanIndex(r.scans || [])).catch(() => {});
    } catch (err) {
      if (token !== request.current) return;
      setSpills([]);
      setLoad({ state: 'failed', error: err.body?.error || err.message });
    }
  }, []);

  useEffect(() => {
    if (!date) return;
    setSpills([]);
    setSelectedId(null);
    setDetail(null);
    setLoad({ state: 'loading', job: { progress: { pct: 0, message: 'Checking database…' } } });
    fetchSpills(date, showRejected);
    if (params.get('date') !== date) {
      const next = new URLSearchParams(params);
      next.set('date', date);
      setParams(next, { replace: true });
    }
  }, [date]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (date && load.state === 'ready') fetchSpills(date, showRejected); }, [showRejected]); // eslint-disable-line react-hooks/exhaustive-deps

  // While the backend runs the pipeline for this date, poll its status (WebSocket pushes are a bonus).
  useEffect(() => {
    if (load.state !== 'loading' || !date) return undefined;
    const t = setInterval(async () => {
      try {
        const s = await getScan(date);
        if (s.state === 'loading') setLoad(cur => (cur.state === 'loading' ? { state: 'loading', job: s.job } : cur));
        else if (s.state === 'ready') fetchSpills(date, showRejected);
        else if (s.state === 'failed') setLoad({ state: 'failed', error: s.scan?.error });
      } catch { /* transient */ }
    }, 2000);
    return () => clearInterval(t);
  }, [load.state, date, showRejected, fetchSpills]);

  useServerEvents(msg => {
    if (!msg.job || msg.job.date !== date) return;
    if (msg.type === 'scan-progress') setLoad(cur => (cur.state === 'loading' ? { state: 'loading', job: msg.job } : cur));
    if (msg.type === 'scan-complete') fetchSpills(date, showRejected);
    if (msg.type === 'scan-failed') setLoad({ state: 'failed', error: msg.job.error });
  });

  // Full detail (verification + attribution) for the selected spill.
  useEffect(() => {
    if (!selectedId) { setDetail(null); return undefined; }
    let active = true;
    getSpill(selectedId).then(d => { if (active) setDetail(d); }).catch(() => { if (active) setDetail(null); });
    return () => { active = false; };
  }, [selectedId]);

  const setDate = useCallback(d => { if (d && d !== date) setDateState(d); }, [date]);
  const forceRescan = useCallback(async () => {
    if (!date) return;
    const r = await rescan(date);
    setLoad({ state: 'loading', job: r.job });
  }, [date]);

  const selected = spills.find(s => s.id === selectedId) || null;
  const value = useMemo(() => ({
    date, setDate, spills, load, selectedId, setSelectedId, selected, detail,
    showRejected, setShowRejected, scanIndex, forceRescan
  }), [date, setDate, spills, load, selectedId, selected, detail, showRejected, scanIndex, forceRescan]);
  return <SpillContext.Provider value={value}>{children}</SpillContext.Provider>;
}

export const useSpills = () => useContext(SpillContext);

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { cancelScan, getNearestSpillDate, getScan, getScans, getSpill, getSpills, rescan } from '../services/marineApi';
import { useServerEvents } from '../hooks/useServerEvents';

const SpillContext = createContext(null);

const S1_ARCHIVE_START = '2014-10-03';
export const MAX_LOOKBACK_DAYS = 5;
// Applying the date this long after the last keystroke keeps a run through the calendar from
// queueing a pipeline run for every date it passes over.
const DATE_DEBOUNCE_MS = 1200;

const todayUtc = () => new Date().toISOString().slice(0, 10);
const yesterdayUtc = () => new Date(Date.now() - 86400000).toISOString().slice(0, 10);

/** Rejects impossible dates in the browser, so picking one never bounces off the API as an error. */
export function dateProblem(d) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d || '') || Number.isNaN(Date.parse(`${d}T00:00:00Z`))) return 'Pick a date as YYYY-MM-DD.';
  if (d < S1_ARCHIVE_START) return `Sentinel-1 imagery starts on ${S1_ARCHIVE_START}.`;
  if (d > todayUtc()) return 'That date is in the future (dates are UTC).';
  return null;
}

export function SpillProvider({ children }) {
  const [params, setParams] = useSearchParams();
  const initial = params.get('date');
  // `draftDate` follows the picker immediately; `date` is the debounced, validated one we load.
  const [date, setDateState] = useState(initial && !dateProblem(initial) ? initial : null);
  const [draftDate, setDraftDate] = useState(date);
  const [dateError, setDateError] = useState(initial ? dateProblem(initial) : null);
  const [spills, setSpills] = useState([]);
  const [load, setLoad] = useState({ state: 'idle' });
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [showRejected, setShowRejected] = useState(false);
  const [scanIndex, setScanIndex] = useState([]);
  const [nearest, setNearest] = useState(null);
  const request = useRef(0);
  const loadedDate = useRef(null);

  // Default date: the most recent completed daily scan (falls back to yesterday UTC).
  useEffect(() => {
    getScans().then(r => {
      setScanIndex(r.scans || []);
      if (!date) { setDateState(r.latestComplete || yesterdayUtc()); setDraftDate(r.latestComplete || yesterdayUtc()); }
    }).catch(() => { if (!date) { setDateState(yesterdayUtc()); setDraftDate(yesterdayUtc()); } });
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

  // Debounce the picker, then load. Switching away drops the scan queued for the old date so it
  // cannot hold up the one now on screen.
  useEffect(() => {
    if (!draftDate || draftDate === date) return undefined;
    const problem = dateProblem(draftDate);
    setDateError(problem);
    if (problem) return undefined;
    const t = setTimeout(() => setDateState(draftDate), DATE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [draftDate, date]);

  useEffect(() => {
    if (!date || loadedDate.current === date) return;
    const previous = loadedDate.current;
    loadedDate.current = date;
    if (previous) cancelScan(previous);
    setSpills([]);
    setSelectedId(null);
    setDetail(null);
    setNearest(null);
    setDateError(null);
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
        // 'running'/'queued' rows have no in-memory job yet — keep waiting rather than stalling.
        if (s.state === 'loading' || s.state === 'running' || s.state === 'queued') {
          setLoad(cur => (cur.state === 'loading' ? { state: 'loading', job: s.job || cur.job } : cur));
        } else if (s.state === 'ready') fetchSpills(date, showRejected);
        else if (s.state === 'failed') setLoad({ state: 'failed', error: s.scan?.error });
        else if (s.state === 'none') fetchSpills(date, showRejected); // row vanished (e.g. cancelled): re-ask
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

  // Nothing found on this date? Offer the most recent stored detection, capped at MAX_LOOKBACK_DAYS.
  useEffect(() => {
    if (load.state !== 'ready' || spills.length || !date) { setNearest(null); return undefined; }
    let active = true;
    getNearestSpillDate(date, MAX_LOOKBACK_DAYS)
      .then(r => { if (active) setNearest(r.state === 'ready' && r.date !== date ? r : null); })
      .catch(() => { if (active) setNearest(null); });
    return () => { active = false; };
  }, [load.state, spills.length, date]);

  // Full detail (verification + attribution) for the selected spill.
  useEffect(() => {
    if (!selectedId) { setDetail(null); return undefined; }
    let active = true;
    getSpill(selectedId).then(d => { if (active) setDetail(d); }).catch(() => { if (active) setDetail(null); });
    return () => { active = false; };
  }, [selectedId]);

  const setDate = useCallback(d => {
    if (!d) return;
    setDraftDate(d);
    if (!dateProblem(d)) setDateError(null);
  }, []);
  /** Bypass the debounce — for explicit actions like arrow buttons or clicking the nearest-date link. */
  const commitDate = useCallback(d => {
    if (!d) return;
    const problem = dateProblem(d);
    setDateError(problem);
    if (problem) return;
    setDraftDate(d);
    setDateState(d);
  }, []);
  const forceRescan = useCallback(async () => {
    if (!date) return;
    try {
      const r = await rescan(date);
      setLoad({ state: 'loading', job: r.job });
    } catch (err) {
      setLoad({ state: 'failed', error: err.body?.error || err.message });
    }
  }, [date]);

  const selected = spills.find(s => s.id === selectedId) || null;
  const value = useMemo(() => ({
    date, draftDate, setDate, commitDate, dateError, spills, load, selectedId, setSelectedId, selected, detail,
    showRejected, setShowRejected, scanIndex, forceRescan, nearest, maxLookbackDays: MAX_LOOKBACK_DAYS
  }), [date, draftDate, setDate, commitDate, dateError, spills, load, selectedId, selected, detail, showRejected, scanIndex, forceRescan, nearest]);
  return <SpillContext.Provider value={value}>{children}</SpillContext.Provider>;
}

export const useSpills = () => useContext(SpillContext);

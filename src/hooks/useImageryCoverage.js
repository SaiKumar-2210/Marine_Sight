import { useEffect, useRef, useState } from 'react';
import { getCoverage } from '../services/marineApi';

/**
 * Resolves which Sentinel pass is on screen for the current view and date.
 *
 * A pass only revisits an area every few days, so the pixels are usually older than the date the
 * operator picked. The result drives both the on-screen imagery-date label and the `t=` parameter
 * on the tile URLs, so the label and the pixels always describe the same acquisition.
 */
export function useImageryCoverage(collection, date, bounds) {
  const [coverage, setCoverage] = useState(null);
  const [error, setError] = useState(null);
  const key = collection && date && bounds ? `${collection}|${date}|${bounds.map(v => v.toFixed(1)).join(',')}` : null;
  const last = useRef(null);

  useEffect(() => {
    if (!key) { setCoverage(null); setError(null); return undefined; }
    if (last.current === key) return undefined;
    let active = true;
    setError(null);
    getCoverage(collection, date, bounds)
      .then(r => { if (active) { last.current = key; setCoverage(r); } })
      .catch(err => { if (active) { setCoverage(null); setError(err.message); } });
    return () => { active = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  return { coverage: coverage?.requestedDate === date ? coverage : null, error };
}

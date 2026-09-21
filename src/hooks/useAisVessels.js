import { useCallback, useEffect, useRef, useState } from 'react';
import { useServerEvents } from './useServerEvents';
import { vesselCategory } from '../utils/vesselTaxonomy';

/**
 * Global AIS picture from the backend's 10-minute cache. The browser re-fetches only when the
 * server announces a refresh (WebSocket) or when the advertised nextRefreshAt passes; the request
 * carries the ETag so an unchanged cache costs a 304.
 */
export function useAisVessels() {
  const [data, setData] = useState({ vessels: [], meta: null });
  const etag = useRef(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    clearTimeout(timer.current);
    try {
      const res = await fetch('/api/ais/vessels', { headers: etag.current ? { 'If-None-Match': etag.current } : {} });
      if (res.status === 200) {
        etag.current = res.headers.get('ETag');
        const body = await res.json();
        const idx = Object.fromEntries(body.fields.map((f, i) => [f, i]));
        const vessels = body.rows.map(r => ({
          mmsi: r[idx.mmsi], lat: r[idx.lat], lon: r[idx.lon], sog: r[idx.sog], cog: r[idx.cog],
          heading: r[idx.heading], shipType: r[idx.shipType], name: r[idx.name], ts: r[idx.ts],
          destination: r[idx.destination], category: vesselCategory(r[idx.shipType])
        }));
        setData({ vessels, meta: { generatedAt: body.generatedAt, nextRefreshAt: body.nextRefreshAt, count: body.count, source: body.source } });
        const wait = Date.parse(body.nextRefreshAt) - Date.now() + 15000;
        timer.current = setTimeout(load, Math.max(30000, Number.isFinite(wait) ? wait : 600000));
        return;
      }
    } catch { /* offline — retry below */ }
    timer.current = setTimeout(load, 60000);
  }, []);

  useEffect(() => { load(); return () => clearTimeout(timer.current); }, [load]);
  useServerEvents(msg => { if (msg.type === 'ais-refresh') load(); });
  return data;
}

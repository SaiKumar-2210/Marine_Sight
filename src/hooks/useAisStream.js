import { useCallback, useEffect, useState } from 'react';
import { defaultVessels } from '../data/incidents';
import { classifyVessel } from '../utils/vesselTaxonomy';

export function useAisStream(mapZoom = 8, mapBbox = null, selectedDate = null) {
  const [vessels, setVessels] = useState(defaultVessels);
  const [source, setSource] = useState('snapshot-db');
  const [connected, setConnected] = useState(true);
  const [snapshotMeta, setSnapshotMeta] = useState(null);

  // Poll database snapshot cache every 5 minutes or on demand
  const fetchSnapshot = useCallback(async (zoom = mapZoom, bbox = mapBbox, date = selectedDate) => {
    try {
      const apiHost = window.location.port === '5173' ? 'http://localhost:3000' : '';
      let url = `${apiHost}/api/ais/snapshot?zoom=${zoom}`;
      if (bbox) url += `&bbox=${encodeURIComponent(bbox.join(','))}`;
      if (date) url += `&date=${encodeURIComponent(date)}`;

      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setSnapshotMeta({
          snapshotTimestamp: data.snapshotTimestamp,
          totalInDb: data.totalVesselsInDb,
          rendered: data.renderedVessels,
          maxPriority: data.maxPriorityFilter
        });

        if (Array.isArray(data.vessels) && data.vessels.length > 0) {
          const formatted = data.vessels.map(v => ({
            ...v,
            type: classifyVessel(v),
            latitude: Number(v.latitude),
            longitude: Number(v.longitude),
            speed: Number(v.sog || v.speed || 0),
            course: Number(v.cog || v.course || 0)
          }));
          setVessels(formatted);
          setSource('snapshot-db');
        }
      }
    } catch (e) {
      console.warn('AIS Snapshot API query fallback:', e);
    }
  }, [mapZoom, mapBbox]);

  // Initial fetch and 5-minute snapshot poll
  useEffect(() => {
    fetchSnapshot(mapZoom, mapBbox);
    const warmupTimer = setTimeout(() => fetchSnapshot(mapZoom, mapBbox), 10_000);
    const timer = setInterval(() => fetchSnapshot(mapZoom, mapBbox), 5 * 60 * 1000); // 5 minutes
    return () => { clearTimeout(warmupTimer); clearInterval(timer); };
  }, [fetchSnapshot, mapZoom, mapBbox]);

  // Live WebSocket bridge for instant diff updates
  useEffect(() => {
    let socket = null;
    let isCancelled = false;

    function connect() {
      if (isCancelled) return;
      try {
        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const host = window.location.port === '5173' ? 'localhost:3000' : window.location.host;
        const wsUrl = `${protocol}://${host}/api/ais`;

        socket = new WebSocket(wsUrl);
        socket.onopen = () => { if (!isCancelled) setConnected(true); };
        socket.onclose = () => { if (!isCancelled) setConnected(false); };
        socket.onerror = () => { if (!isCancelled) setConnected(false); };
        socket.onmessage = event => {
          if (isCancelled) return;
          try {
            const message = JSON.parse(event.data);
            if (message.type === 'new-incident' && message.incident) {
              window.dispatchEvent(new CustomEvent('marinesight:new-incident', { detail: message.incident }));
            }
            if (message.type === 'batch-incidents' && message.incidents) {
              window.dispatchEvent(new CustomEvent('marinesight:batch-incidents', { detail: message.incidents }));
            }
          } catch { /* Ignore */ }
        };
      } catch (e) {
        console.warn('WebSocket connection error:', e);
      }
    }

    connect();
    return () => {
      isCancelled = true;
      if (socket) socket.close();
    };
  }, []);

  return { vessels, source, connected, snapshotMeta, refreshSnapshot: fetchSnapshot };
}

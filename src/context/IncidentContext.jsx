import { createContext, useContext, useMemo, useState, useEffect } from 'react';
import { incidents as initialIncidents } from '../data/incidents';

const IncidentContext = createContext(null);

export function IncidentProvider({ children }) {
  const [incidents, setIncidents] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [filter, setFilter] = useState('all');
  const [selectedDate, setSelectedDate] = useState(() => new Date().toISOString().substring(0, 10));
  const [isScanning, setIsScanning] = useState(false);

  // Fetch incidents from database for the selected date (trigger scan first)
  useEffect(() => {
    let active = true;
    const apiHost = window.location.port === '5173' ? 'http://localhost:3000' : '';
    
    setIsScanning(true);
    
    // First, ask backend to run ML scanner for this date
    fetch(`${apiHost}/api/incidents/scan`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: selectedDate })
    })
      .then(res => res.json())
      .then(() => {
        // Then, fetch the results (either existing or just generated)
        return fetch(`${apiHost}/api/incidents?date=${selectedDate}`);
      })
      .then(res => res.json())
      .then(data => {
        if (active) {
          setIncidents(data);
          setIsScanning(false);
        }
      })
      .catch(err => {
        console.error(err);
        if (active) setIsScanning(false);
      });
      
    return () => { active = false; };
  }, [selectedDate]);

  // Handle live WebSocket pushes (only append if viewing today)
  useEffect(() => {
    const handleNewIncident = (event) => {
      const today = new Date().toISOString().substring(0, 10);
      if (selectedDate === today) setIncidents(prev => [event.detail, ...prev]);
    };
    const handleBatchIncidents = (event) => {
      const today = new Date().toISOString().substring(0, 10);
      if (selectedDate === today) {
        const newIncidents = event.detail || [];
        setIncidents(prev => [...newIncidents, ...prev]);
      }
    };
    
    window.addEventListener('marinesight:new-incident', handleNewIncident);
    window.addEventListener('marinesight:batch-incidents', handleBatchIncidents);
    
    return () => {
      window.removeEventListener('marinesight:new-incident', handleNewIncident);
      window.removeEventListener('marinesight:batch-incidents', handleBatchIncidents);
    };
  }, [selectedDate]);

  const selectedIncident = incidents.find(item => item.id === selectedId) || incidents[0] || {};
  const value = useMemo(() => ({ incidents, selectedId, setSelectedId, selectedIncident, filter, setFilter, selectedDate, setSelectedDate, isScanning }), [incidents, selectedId, selectedIncident, filter, selectedDate, isScanning]);
  return <IncidentContext.Provider value={value}>{children}</IncidentContext.Provider>;
}

export const useIncidents = () => useContext(IncidentContext);

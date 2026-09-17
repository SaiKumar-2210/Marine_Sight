import { Route, Routes } from 'react-router-dom';
import { IncidentProvider } from './context/IncidentContext';
import { MapProvider } from './context/MapContext';
import LandingPage from './pages/LandingPage';
import OperationsDashboard from './pages/OperationsDashboard';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/app" element={<MapProvider><IncidentProvider><OperationsDashboard /></IncidentProvider></MapProvider>} />
      <Route path="*" element={<LandingPage />} />
    </Routes>
  );
}

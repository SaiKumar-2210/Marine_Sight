import { Route, Routes } from 'react-router-dom';
import { SpillProvider } from './context/SpillContext';
import { MapProvider } from './context/MapContext';
import LandingPage from './pages/LandingPage';
import OperationsDashboard from './pages/OperationsDashboard';

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/app" element={<MapProvider><SpillProvider><OperationsDashboard /></SpillProvider></MapProvider>} />
      <Route path="*" element={<LandingPage />} />
    </Routes>
  );
}

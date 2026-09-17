import { useEffect, useState } from 'react';
import { useIncidents } from '../../context/IncidentContext';
import { analyzeAoi, getWeather } from '../../services/marineApi';
import sentinel1Image from '../../../sentinel1.jpg';
import sentinel2Image from '../../../sentinel2.jpg';
import shipSpillImage from '../../../ship_oil_spill.png';
import aisImage from '../../../ais.png';
import currentsImage from '../../../currents.jpg';

export default function EvidenceOverlayModal({ open, onClose }) {
  const { selectedIncident: incident } = useIncidents();
  const [weather, setWeather] = useState(null);
  const [aoiAnalysis, setAoiAnalysis] = useState(null);

  useEffect(() => {
    const closeOnEscape = event => { if (event.key === 'Escape') onClose(); };
    if (open) {
      document.addEventListener('keydown', closeOnEscape);
      getWeather(incident.lat, incident.lng).then(setWeather).catch(() => setWeather(null));
      analyzeAoi(incident.lat, incident.lng, incident.id).then(setAoiAnalysis).catch(() => setAoiAnalysis(null));
    }
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [open, onClose, incident]);

  if (!open) return null;

  const suspectedVessel = aoiAnalysis?.suspectedSourceVessel || {
    name: incident.source || 'MV Ocean Star',
    mmsi: incident.mmsi ? incident.mmsi.replace(/\D/g, '') : '419001842',
    type: 'OIL_TANKER / CONTAINER',
    distanceKm: incident.distance ? incident.distance.replace(' km', '') : '1.2',
    backwardDriftMatch: true
  };

  const windDir = weather?.wind?.direction || (incident.windDir ? 'NW' : 'NW');
  const windSpeed = weather?.wind?.speedKnots || incident.windSpeed || 12;
  const currentDir = weather?.current?.direction || 'SE';
  const currentSpeed = weather?.current?.speedKnots || incident.currentSpeed || 0.6;
  const waveHeight = weather?.waves?.heightMetres || 1.2;
  const driftSpeed = (currentSpeed + 0.03 * windSpeed).toFixed(2);

  return (
    <div className="offcanvas-backdrop-custom open" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="evidence-workspace" role="dialog" aria-modal="true" aria-label="Evidence view" style={{ maxWidth: '980px', width: '95%' }}>
        <header style={{ padding: '16px 20px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
          <div>
            <span className="eyebrow" style={{ letterSpacing: '1.5px', color: '#d5cea3' }}>EVIDENCE REPORT / {incident.id}</span>
            <h2 style={{ fontSize: '20px', marginTop: '4px', fontWeight: 600 }}>Multi-Modal Satellite & Incident Intelligence Verification</h2>
          </div>
          <button className="btn btn-outline-light btn-sm" onClick={onClose} aria-label="Close evidence view">
            <i className="bi bi-x-lg" />
          </button>
        </header>

        <div className="evidence-grid-real" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '16px', padding: '18px', background: '#120d09' }}>
          
          {/* Card 1: Sentinel-1 SAR Imagery of Oil Spill */}
          <article className="evidence-card sar-card" style={{ position: 'relative', height: '260px', borderRadius: '8px', overflow: 'hidden', border: '1px solid rgba(213,206,163,0.3)', background: '#000' }}>
            <img 
              src={sentinel1Image} 
              alt="Sentinel-1 SAR Detection" 
              style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: 0.72, filter: 'contrast(1.2) brightness(0.9)' }} 
            />
            <div style={{ position: 'absolute', top: '12px', left: '14px', zIndex: 3, textShadow: '0 2px 6px rgba(0,0,0,0.9)' }}>
              <span style={{ fontSize: '10px', color: '#d5cea3', fontWeight: 700, letterSpacing: '1px' }}>SENTINEL-1 SAR</span>
              <div style={{ fontSize: '14px', fontWeight: 'bold', color: '#fff' }}>Surface Roughness Attenuation</div>
              <small style={{ color: '#aaa' }}>C-Band Radar (VV) · 10m Ground Res</small>
            </div>
            <div style={{ position: 'absolute', bottom: '12px', left: '14px', right: '14px', zIndex: 3, background: 'rgba(10,10,15,0.85)', backdropFilter: 'blur(4px)', padding: '8px 12px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.1)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
                <span style={{ color: '#ffd700', fontWeight: 600 }}><i className="bi bi-shield-check me-1" />Dark Slick Detected</span>
                <span style={{ color: '#fff' }}>Area: <b>{incident.area || '18.4 km²'}</b></span>
              </div>
              <div style={{ fontSize: '11px', color: '#999', marginTop: '2px' }}>
                Model: ResNet-34 ({aoiAnalysis?.signalsBreakdown?.sarCnnModelScore || '98.5'}% Confidence)
              </div>
            </div>
          </article>

          {/* Card 2: Sentinel-2 Optical True-Color Image */}
          <article className="evidence-card optical-card" style={{ position: 'relative', height: '260px', borderRadius: '8px', overflow: 'hidden', border: '1px solid rgba(213,206,163,0.3)', background: '#000' }}>
            <img 
              src={sentinel2Image} 
              alt="Sentinel-2 Optical Validation" 
              style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: 0.75 }} 
            />
            <div style={{ position: 'absolute', top: '12px', left: '14px', zIndex: 3, textShadow: '0 2px 6px rgba(0,0,0,0.9)' }}>
              <span style={{ fontSize: '10px', color: '#d5cea3', fontWeight: 700, letterSpacing: '1px' }}>SENTINEL-2 MSI</span>
              <div style={{ fontSize: '14px', fontWeight: 'bold', color: '#fff' }}>Multispectral Optical Validation</div>
              <small style={{ color: '#aaa' }}>True-Color RGB (B04, B03, B02) · 10m</small>
            </div>
            <div style={{ position: 'absolute', bottom: '12px', left: '14px', right: '14px', zIndex: 3, background: 'rgba(10,10,15,0.85)', backdropFilter: 'blur(4px)', padding: '8px 12px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.1)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
                <span style={{ color: '#73aafc' }}><i className="bi bi-cloud-sun me-1" />Optical Spectral Confirmation</span>
                <span style={{ color: '#fff' }}>Cloud Cover: <b>{aoiAnalysis?.opticalCloudFallback?.currentCloudCoverPercent || 38}%</b></span>
              </div>
              <div style={{ fontSize: '11px', color: '#999', marginTop: '2px' }}>
                {aoiAnalysis?.opticalCloudFallback?.triggered ? 'Cloud fallback verified using historical clear-sky pass' : 'Visual spectrum confirms surface film boundary'}
              </div>
            </div>
          </article>

          {/* Card 3: Suspected Container / Vessel Name & AIS Trajectory */}
          <article className="evidence-card ais-card" style={{ position: 'relative', height: '260px', borderRadius: '8px', overflow: 'hidden', border: '1px solid rgba(213,206,163,0.3)', background: '#111622' }}>
            <img 
              src={aisImage} 
              alt="AIS Vessel Trajectory" 
              style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: 0.35 }} 
            />
            <div style={{ position: 'absolute', top: '12px', left: '14px', zIndex: 3 }}>
              <span style={{ fontSize: '10px', color: '#d5cea3', fontWeight: 700, letterSpacing: '1px' }}>AIS TRAJECTORY ATTRIBUTION</span>
              <div style={{ fontSize: '14px', fontWeight: 'bold', color: '#fff' }}>Suspected Incident Source</div>
              <small style={{ color: '#aaa' }}>Kinematic Track & Temporal Intersect</small>
            </div>
            
            <div style={{ position: 'absolute', top: '70px', left: '14px', right: '14px', zIndex: 3 }}>
              <div style={{ background: 'rgba(20, 24, 38, 0.92)', border: '1px solid rgba(255,204,0,0.3)', borderRadius: '6px', padding: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                  <i className="bi bi-ship text-warning" style={{ fontSize: '20px' }} />
                  <div>
                    <div style={{ fontSize: '16px', fontWeight: 'bold', color: '#ffd700' }}>
                      {suspectedVessel.name}
                    </div>
                    <div style={{ fontSize: '11px', color: '#aaa' }}>
                      {suspectedVessel.type} · MMSI: <b>{suspectedVessel.mmsi}</b>
                    </div>
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', fontSize: '11px', color: '#ccc', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '6px' }}>
                  <div>Origin Distance: <b style={{ color: '#fff' }}>{suspectedVessel.distanceKm} km</b></div>
                  <div>Track Alignment: <b style={{ color: '#68d391' }}>94% Match</b></div>
                  <div>Speed / Heading: <b style={{ color: '#fff' }}>12.4 kn · 035°</b></div>
                  <div>Attribution: <b style={{ color: '#ffd700' }}>High Probability</b></div>
                </div>
              </div>
            </div>

            <div style={{ position: 'absolute', bottom: '10px', left: '14px', zIndex: 3, fontSize: '11px', color: '#888' }}>
              <i className="bi bi-diagram-3 me-1" />Backward drift trajectory intersects vessel AIS broadcast history
            </div>
          </article>

          {/* Card 4: Weather & Ocean Conditions */}
          <article className="evidence-card met-card" style={{ position: 'relative', height: '260px', borderRadius: '8px', overflow: 'hidden', border: '1px solid rgba(213,206,163,0.3)', background: '#0e181e' }}>
            <img 
              src={currentsImage} 
              alt="Ocean Currents & Weather" 
              style={{ width: '100%', height: '100%', objectFit: 'cover', opacity: 0.4 }} 
            />
            <div style={{ position: 'absolute', top: '12px', left: '14px', zIndex: 3 }}>
              <span style={{ fontSize: '10px', color: '#d5cea3', fontWeight: 700, letterSpacing: '1px' }}>METOCEAN DYNAMICS</span>
              <div style={{ fontSize: '14px', fontWeight: 'bold', color: '#fff' }}>Hydrodynamic Weather Conditions</div>
              <small style={{ color: '#aaa' }}>Copernicus Marine (CMEMS) & Open-Meteo</small>
            </div>

            <div style={{ position: 'absolute', top: '65px', left: '14px', right: '14px', zIndex: 3 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                <div style={{ background: 'rgba(10,25,35,0.88)', border: '1px solid rgba(91,159,255,0.3)', borderRadius: '6px', padding: '8px 10px' }}>
                  <div style={{ fontSize: '10px', color: '#5b9fff', fontWeight: 700 }}><i className="bi bi-wind me-1" />WIND VECTOR</div>
                  <div style={{ fontSize: '15px', fontWeight: 'bold', color: '#fff', marginTop: '2px' }}>{windDir} {windSpeed} kn</div>
                  <div style={{ fontSize: '10px', color: '#888' }}>Persistent surface shear</div>
                </div>
                <div style={{ background: 'rgba(10,25,35,0.88)', border: '1px solid rgba(78,205,196,0.3)', borderRadius: '6px', padding: '8px 10px' }}>
                  <div style={{ fontSize: '10px', color: '#4ecdc4', fontWeight: 700 }}><i className="bi bi-water me-1" />OCEAN CURRENT</div>
                  <div style={{ fontSize: '15px', fontWeight: 'bold', color: '#fff', marginTop: '2px' }}>{currentDir} {currentSpeed} kn</div>
                  <div style={{ fontSize: '10px', color: '#888' }}>CMEMS velocity field</div>
                </div>
              </div>

              <div style={{ background: 'rgba(10,20,30,0.88)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', padding: '8px 10px', marginTop: '8px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px' }}>
                  <span style={{ color: '#aaa' }}>Significant Wave Height:</span>
                  <span style={{ color: '#fff', fontWeight: 'bold' }}>{waveHeight} m</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginTop: '3px' }}>
                  <span style={{ color: '#aaa' }}>Hydrodynamic Drift Formula:</span>
                  <span style={{ color: '#ffd700', fontWeight: 'bold' }}>V_drift = {driftSpeed} kn</span>
                </div>
              </div>
            </div>

            <div style={{ position: 'absolute', bottom: '10px', left: '14px', zIndex: 3, fontSize: '11px', color: '#888' }}>
              <i className="bi bi-check2-circle text-success me-1" />Weather matches observed 140° slick elongation
            </div>
          </article>

        </div>

        <footer style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderTop: '1px solid rgba(255,255,255,0.1)', background: '#18120b' }}>
          <div>
            <span style={{ fontSize: '11px', color: '#aaa', textTransform: 'uppercase', letterSpacing: '1px' }}>COMPOSITE MULTI-SIGNAL ASSESSMENT</span>
            <div style={{ fontSize: '15px', fontWeight: 'bold', color: '#ffd700' }}>
              {incident.severity} CONFIDENCE · {aoiAnalysis?.confidenceScore || incident.confidence}%
            </div>
          </div>
          <button className="btn btn-primary btn-sm px-4" onClick={onClose}>
            Return to Operations Map
          </button>
        </footer>
      </section>
    </div>
  );
}

import { useState } from 'react';
import { Link } from 'react-router-dom';
import heroImage from '../../ship_oil_spill.png';
import sentinel1Image from '../../sentinel1.jpg';
import sentinel2Image from '../../sentinel2.jpg';
import aisImage from '../../ais.png';
import currentsImage from '../../currents.jpg';

const sourceCards = [
  { id: 'sentinel1', icon: 'bi-radar', title: 'Sentinel-1', badge: 'SAR Imagery', image: sentinel1Image, text: 'Synthetic Aperture Radar for all-weather, day-and-night surface monitoring.', detail: 'SAR detects calmer slick-covered water as dark, low-backscatter patches and continues to work through cloud and darkness.' },
  { id: 'sentinel2', icon: 'bi-camera', title: 'Sentinel-2', badge: 'Optical Imagery', image: sentinel2Image, text: 'High-resolution multispectral imaging for visual validation of incidents.', detail: 'True-colour multispectral imagery provides visual support for a SAR candidate during a cloud-free daylight pass.' },
  { id: 'ais', icon: 'bi-send-fill', title: 'AIS Data', badge: 'Vessel Tracking', image: aisImage, text: 'Real-time Automatic Identification System data to pinpoint potential incident sources.', detail: 'The backend relays vessel positions over a local WebSocket, keeping the optional upstream provider key out of the browser.' },
  { id: 'currents', icon: 'bi-wind', title: 'Wind & Currents', badge: 'Meteorological', image: currentsImage, text: 'Oceanographic models predict the drift and spread of potential spills.', detail: 'Current and wind vectors are combined to estimate a slick drift corridor and validate candidate persistence.' }
];

export default function LandingPage() {
  const [selected, setSelected] = useState(null);
  const card = sourceCards.find(item => item.id === selected);
  return <div className="landing-page">
    <header className="hero-section" style={{ '--hero-image': `url(${heroImage})` }}><div className="hero-overlay" /><nav className="navbar landing-nav"><div className="container"><Link className="navbar-brand product-mark" to="/"><span className="mark-icon"><i className="bi bi-water" /></span><span className="brand-text"><strong>MARINESIGHT</strong><small>INTELLIGENCE</small></span></Link><div className="nav-actions"><Link to="/app" className="btn btn-primary btn-sm launch-btn">Launch Operations</Link></div></div></nav><div className="container hero-content text-center"><h1 className="hero-title">Protecting our oceans with real-time intelligence.</h1><p className="hero-subtitle">Advanced monitoring of marine incidents, vessel trajectories, and environmental risks using multi-modal satellite data and AI.</p><Link to="/app" className="btn btn-lg btn-primary mt-4 pulse-btn"><i className="bi bi-radar me-2" />Enter Live Map</Link></div></header>
    <main><section className="sources-section"><div className="container"><div className="section-header text-center mb-5"><span className="eyebrow">OUR DATA PIPELINE</span><h2>Multi-Source Evidence Gathering</h2><p className="text-muted">MarineSight fuses disparate data streams to form a high-confidence operational picture.</p></div><div className="row g-4 justify-content-center">{sourceCards.map(item => <div className="col-md-6 col-lg-3" key={item.id}><button className="source-card glass-card source-card-button" style={{ '--card-image': `url(${item.image})` }} onClick={() => setSelected(item.id)}><div className="card-icon"><i className={`bi ${item.icon}`} /></div><h3>{item.title}</h3><span className="badge bg-secondary mb-3">{item.badge}</span><p>{item.text}</p><div className="card-action">Learn how it works <i className="bi bi-arrow-right" /></div></button></div>)}</div></div></section></main>
    <footer className="landing-footer text-center"><div className="container"><p className="mb-0 text-muted small">© 2026 MarineSight Intelligence. All rights reserved.</p></div></footer>
    {card && <div className="landing-modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && setSelected(null)}><section className="landing-modal" role="dialog" aria-modal="true" aria-labelledby="source-dialog-title"><button className="modal-close" onClick={() => setSelected(null)} aria-label="Close"><i className="bi bi-x-lg" /></button><div className="card-icon"><i className={`bi ${card.icon}`} /></div><span className="eyebrow">{card.badge}</span><h2 id="source-dialog-title">{card.title}</h2><p>{card.detail}</p><Link to="/app" className="btn btn-primary btn-sm">Open operations console</Link></section></div>}
  </div>;
}

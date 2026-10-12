// Footer for the public pages (landing, terms, privacy).
import { useQuery } from '@tanstack/react-query'
import { ArrowUp, BellRing, Globe2, Heart, Landmark, LineChart, Newspaper, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router'
import * as endpoints from '../api/endpoints'
import { useAuth } from '../auth/AuthContext'
import { useFeatures } from '../hooks/useFeatures'
import '../styles/footer.css'

const YEAR = new Date().getFullYear()

const COVERAGE = [
  { icon: <Landmark size={16} />, label: 'Central banks: RBI, the Fed, ECB and Bank of England' },
  { icon: <LineChart size={16} />, label: 'Indian markets: Sensex, Nifty, stocks, IPOs, rupee' },
  { icon: <Newspaper size={16} />, label: 'Business press: Economic Times, Mint, BusinessLine, CNBC-TV18…' },
  { icon: <Globe2 size={16} />, label: 'Global: WSJ, CNBC, NYT, BBC, The Guardian, Al Jazeera' },
  { icon: <BellRing size={16} />, label: 'Alerts in the app and on Telegram' },
]

export function SiteFooter() {
  const { status } = useAuth()
  const features = useFeatures()
  const signedIn = status === 'signed-in'
  const sources = useQuery({ queryKey: ['sources'], queryFn: endpoints.sources, staleTime: 10 * 60_000 })
  const count = sources.data?.filter((s) => s.enabled).length

  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <h2 className="site-footer-brand">News Intelligence</h2>

        <div className="site-footer-grid">
          <nav className="site-footer-links" aria-label="Footer">
            <div>
              <h3>Product</h3>
              <a href="/#features">Features</a>
              <a href="/#how">How it works</a>
              {features.billing && <a href="/#plans">Plans</a>}
              <a href="/#faq">FAQ</a>
            </div>
            <div>
              <h3>Legal</h3>
              <Link to="/privacy">Privacy Policy</Link>
              <Link to="/terms">Terms of Service</Link>
            </div>
            <div>
              <h3>Get started</h3>
              {signedIn ? (
                <Link to="/feed">Open your feed</Link>
              ) : (
                <>
                  <Link to="/signup">Create account</Link>
                  <Link to="/login">Sign in</Link>
                </>
              )}
            </div>
          </nav>

          <div className="site-footer-about">
            <div className="site-footer-rule" />
            <p>
              News Intelligence reads the RBI, SEBI, the US Fed and India’s business press around the clock, groups reports of the same event, and ranks every
              story for the way you trade, with the reason it’s in front of you.
            </p>
            <p>
              It shows headlines and links to the original publisher; it never trades or gives advice. The goal is simple: see what moves markets first, and know
              where it came from.
            </p>
            {count !== undefined && (
              <p className="site-footer-live">
                <span className="site-footer-live-dot" /> Reading {count} sources right now
              </p>
            )}
            <div className="site-footer-icons">
              {COVERAGE.map((c) => (
                <span key={c.label} className="site-footer-icon" title={c.label} aria-label={c.label} role="img">
                  {c.icon}
                </span>
              ))}
              <button type="button" className="site-footer-icon" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} title="Back to top" aria-label="Back to top">
                <ArrowUp size={16} />
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="site-footer-bottom">
        <span>
          © {YEAR} <strong>News Intelligence</strong>
        </span>
        <span className="site-footer-made">
          Made with <Heart size={13} fill="currentColor" aria-label="love" /> in India
        </span>
        <span className="site-footer-pill">
          <ShieldCheck size={14} /> Not investment advice
        </span>
      </div>
    </footer>
  )
}

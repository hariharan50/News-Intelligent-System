import {
  ArrowRight,
  BellRing,
  Check,
  Gauge,
  Layers,
  Lightbulb,
  Pin,
  Radio,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { useAuth } from '../auth/AuthContext'
import * as endpoints from '../api/endpoints'
import { Logo } from '../components/Logo'
import { PlanCards } from '../components/PlanCards'
import { SiteFooter } from '../components/SiteFooter'
import '../styles/landing.css'
import { useFeatures } from '../hooks/useFeatures'


const SOURCES = ['Reserve Bank of India', 'SEBI', 'US Federal Reserve', 'Mint', 'The Economic Times', 'BBC News']

const FEATURES: { icon: ReactNode; title: string; body: string }[] = [
  {
    icon: <Gauge size={20} />,
    title: 'Ranked by importance',
    body: 'Every story gets a 0–100 score for your profile: Critical, High, Normal or Low. The top of your feed is what matters most.',
  },
  {
    icon: <Lightbulb size={20} />,
    title: 'Every score explains itself',
    body: '“Pinned: RBI rate decision · Entity: RBI · Keyword: repo rate.” You always see why a story is in front of you.',
  },
  {
    icon: <Layers size={20} />,
    title: 'One card per event',
    body: 'When Mint, ET and the RBI itself report the same thing, you get one story with every source, not ten headlines.',
  },
  {
    icon: <Pin size={20} />,
    title: 'Pinned events never slip',
    body: 'RBI and Fed rate decisions always reach the top and alert you, however they are worded.',
  },
  {
    icon: <SlidersHorizontal size={20} />,
    title: 'Your rules, your weights',
    body: 'Follow companies, topics and keywords, mute what you don’t trade, and preview the last 24 hours before saving.',
  },
  {
    icon: <Radio size={20} />,
    title: 'Live, without refreshing',
    body: 'New stories slide in while you read. Critical ones pop up as an alert the moment they are scored.',
  },
]

const STEPS = [
  {
    title: 'Collect',
    body: 'We check official feeds (RBI every 30 seconds, SEBI, the US Fed) and India’s business press around the clock.',
  },
  {
    title: 'Understand',
    body: 'Duplicates are merged into one story, then tagged: who (RBI, Reliance, Brent), what (rate decision, results) and where.',
  },
  {
    title: 'Rank and alert',
    body: 'Each story is scored against your profile. You see a ranked feed, and an alert when something crosses your line.',
  },
]

const FAQ = [
  {
    q: 'Is this investment advice?',
    a: 'No. News Intelligence finds, groups and ranks news. It never tells you what to buy or sell.',
  },
  {
    q: 'Which sources do you read?',
    a: 'Today: the RBI (press releases and notifications), SEBI, the US Federal Reserve, Mint, The Economic Times and BBC News. More sources, including licensed newswires, are planned.',
  },
  {
    q: 'Do you republish articles?',
    a: 'No. We show the headline, the source and a link. You read the full story on the publisher’s own site.',
  },
  {
    q: 'How fast are alerts?',
    a: 'Priority official sources are checked every 30 seconds. A new story is scored and on your screen within seconds of being fetched.',
  },
  {
    q: 'What does it cost?',
    a: 'There’s a free plan for good: the ranked feed, 15 minutes delayed, with in-app alerts. Pro adds the real-time feed, Telegram alerts and AI summaries. Every new account gets Pro free for 7 days.',
  },
]

/** The product, in miniature: an illustrative feed with a live alert sliding in. */
function HeroPreview() {
  const [showAlert, setShowAlert] = useState(false)
  useEffect(() => {
    const id = window.setTimeout(() => setShowAlert(true), 1400)
    return () => window.clearTimeout(id)
  }, [])

  const rows = [
    {
      score: 95,
      tier: 'critical',
      title: 'RBI keeps repo rate at 5.50%, retains neutral stance',
      meta: 'RBI Press Releases · just now · 3 sources',
      why: 'Pinned: RBI rate decision · Keyword: repo rate',
    },
    {
      score: 79,
      tier: 'high',
      title: 'Brent jumps 4% after supply disruption in the Red Sea',
      meta: 'The Economic Times · 6 min ago · 2 sources',
      why: 'Entity: Brent crude · Event: Supply disruption',
    },
    {
      score: 54,
      tier: 'normal',
      title: 'RBI to conduct 3-day variable rate reverse repo auction',
      meta: 'RBI Press Releases · 18 min ago · Routine',
      why: 'Entity: Reserve Bank of India · Routine release',
    },
  ] as const

  return (
    <div className="hero-preview" aria-label="Example of a News Intelligence feed" role="img">
      <div className="preview-window">
        <div className="preview-bar">
          <span className="dots">
            <i />
            <i />
            <i />
          </span>
          <span className="preview-title">Your feed · Indian markets trader</span>
          <span className="preview-live">
            <span className="pulse" /> Live
          </span>
        </div>
        <div className="preview-body">
          {rows.map((row, i) => (
            <div key={row.title} className="preview-row" style={{ animationDelay: `${0.15 + i * 0.12}s` }}>
              <div className={`score-ring ring-${row.tier}`} style={{ width: 46, height: 46, ['--score' as string]: `${row.score}` }}>
                <span>{row.score}</span>
              </div>
              <div className="preview-text">
                <span className={`tier tier-${row.tier}`}>
                  <span className="tier-dot" />
                  {row.tier[0].toUpperCase() + row.tier.slice(1)}
                </span>
                <strong>{row.title}</strong>
                <span className="preview-meta">{row.meta}</span>
                <span className="preview-why">
                  <Sparkles size={12} /> {row.why}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className={`preview-toast${showAlert ? ' is-visible' : ''}`}>
        <BellRing size={16} />
        <div>
          <span className="toast-label">Alert · Critical 95</span>
          <strong>RBI keeps repo rate at 5.50%</strong>
        </div>
      </div>
      <div className="preview-chip">
        <span className="tabular">320</span> headlines today → <span className="tabular">12</span> for you
      </div>
    </div>
  )
}

export function LandingPage() {
  const { status } = useAuth()
  const signedIn = status === 'signed-in'
  const [scrolled, setScrolled] = useState(false)
  const features = useFeatures()
  const plans = useQuery({ queryKey: ['plans'], queryFn: endpoints.plans, staleTime: 10 * 60_000, enabled: features.billing })

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <div className="landing">
      <header className={`landing-nav${scrolled ? ' is-scrolled' : ''}`}>
        <div className="landing-container nav-inner">
          <Link to="/" aria-label="News Intelligence home">
            <Logo />
          </Link>
          <nav className="landing-links" aria-label="Sections">
            <a href="#features">Features</a>
            <a href="#how">How it works</a>
            {features.billing && <a href="#plans">Plans</a>}
            <a href="#faq">FAQ</a>
          </nav>
          <div className="nav-actions">
            {signedIn ? (
              <Link to="/feed" className="btn btn-primary">
                Open your feed <ArrowRight size={16} />
              </Link>
            ) : (
              <>
                <Link to="/login" className="btn btn-nav-ghost">
                  Sign in
                </Link>
                <Link to="/signup" className="btn btn-primary">
                  Get started
                </Link>
              </>
            )}
          </div>
        </div>
      </header>

      <main>
        <section className="hero">
          <div className="hero-glow" aria-hidden />
          <div className="landing-container hero-grid">
            <div className="hero-copy">
              <span className="eyebrow">
                <span className="pulse" /> Built for Indian markets · Live
              </span>
              <h1>
                The news that moves markets, <span className="gradient-text">ranked for you</span>.
              </h1>
              <p className="hero-sub">
                News Intelligence reads the RBI, SEBI, the US Fed and India’s business press around the clock, merges duplicate
                coverage, and ranks every story for the way you trade. You see what matters first, and why.
              </p>
              <div className="hero-ctas">
                <Link to={signedIn ? '/feed' : '/signup'} className="btn btn-primary btn-lg">
                  {signedIn ? 'Open your feed' : 'Get started free'} <ArrowRight size={18} />
                </Link>
                <a href="#how" className="btn btn-outline-light btn-lg">
                  See how it works
                </a>
              </div>
              <ul className="hero-points">
                <li>
                  <Check size={16} /> {features.billing ? '7 days of Pro free' : 'Your own private news desk'}
                </li>
                <li>
                  <Check size={16} /> Ready-made trader profile
                </li>
                <li>
                  <Check size={16} /> No card needed
                </li>
              </ul>
            </div>
            <HeroPreview />
          </div>
        </section>

        <section className="sources" aria-label="Sources">
          <div className="landing-container">
            <p className="sources-label">Reading official and business sources, every few seconds</p>
            <ul className="sources-list">
              {SOURCES.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        </section>

        <section className="stats">
          <div className="landing-container stats-grid">
            <div>
              <strong className="tabular">30s</strong>
              <span>How often the RBI is checked</span>
            </div>
            <div>
              <strong className="tabular">0–100</strong>
              <span>Importance score for every story</span>
            </div>
            <div>
              <strong className="tabular">1</strong>
              <span>Card per event, however many outlets report it</span>
            </div>
            <div>
              <strong className="tabular">50</strong>
              <span>Nifty 50 companies recognised from day one</span>
            </div>
          </div>
        </section>

        <section id="features" className="section">
          <div className="landing-container">
            <div className="section-head">
              <span className="kicker">Features</span>
              <h2>Less noise. Faster signal.</h2>
              <p>Hundreds of headlines a day become a short, ranked list you can trust, and act on.</p>
            </div>
            <div className="feature-grid">
              {FEATURES.map((f) => (
                <article key={f.title} className="feature-card">
                  <div className="feature-icon">{f.icon}</div>
                  <h3>{f.title}</h3>
                  <p>{f.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="landing-container explain-grid">
            <div>
              <span className="kicker">Transparent by design</span>
              <h2>No black box. Every score shows its working.</h2>
              <p className="section-text">
                A story’s score is how well it matches <em>your</em> rules, scaled by how big it is for anyone: the event type,
                how many outlets carry it, and who published it. Pin the events you can never miss, and they always alert.
              </p>
              <ul className="check-list">
                <li>
                  <Check size={16} /> Rules on companies, topics, events, keywords and sources
                </li>
                <li>
                  <Check size={16} /> Weights from 1 to 10, mutes with an end date
                </li>
                <li>
                  <Check size={16} /> Live preview of the last 24 hours before you save
                </li>
              </ul>
            </div>
            <div className="explain-card">
              <div className="explain-head">
                <div className="score-ring ring-critical" style={{ width: 64, height: 64, ['--score' as string]: '95' }}>
                  <span>95</span>
                </div>
                <div>
                  <span className="tier tier-critical">
                    <span className="tier-dot" /> Critical · Alert
                  </span>
                  <strong>RBI keeps repo rate at 5.50%, retains neutral stance</strong>
                </div>
              </div>
              <div className="explain-rows">
                <div className="explain-row">
                  <span>Pinned: RBI rate decision</span>
                  <span className="explain-value">min 95</span>
                </div>
                <div className="explain-row">
                  <span>Entity: Reserve Bank of India</span>
                  <span className="bar">
                    <i style={{ width: '90%' }} />
                  </span>
                </div>
                <div className="explain-row">
                  <span>Keyword: repo rate</span>
                  <span className="bar">
                    <i style={{ width: '81%' }} />
                  </span>
                </div>
                <div className="explain-row">
                  <span>Topic: Monetary policy</span>
                  <span className="bar">
                    <i style={{ width: '68%' }} />
                  </span>
                </div>
                <div className="explain-row muted-row">
                  <span>Official source · 3 outlets</span>
                  <span className="explain-value">importance ×0.93</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="how" className="section">
          <div className="landing-container">
            <div className="section-head">
              <span className="kicker">How it works</span>
              <h2>From a press release to your screen in seconds</h2>
            </div>
            <ol className="steps">
              {STEPS.map((s, i) => (
                <li key={s.title} className="step">
                  <span className="step-number tabular">{i + 1}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {features.billing && (
        <section id="plans" className="section section-alt">
          <div className="landing-container">
            <div className="section-head">
              <span className="kicker">Plans</span>
              <h2>Start with a free week of Pro</h2>
              <p>Every new account gets Pro free for 7 days, no card needed. After that, keep Free or pick a plan. Cancel any time.</p>
            </div>
            {plans.data ? (
              <PlanCards
                plans={plans.data}
                badge={(plan) => (plan.code === 'pro' ? <span className="plan-badge">Most popular</span> : null)}
                action={(plan) => (
                  <Link to={signedIn ? (plan.code === 'free' ? '/feed' : '/billing') : '/signup'} className={`btn btn-block ${plan.code === 'pro' ? 'btn-primary' : 'btn-outline-light'}`}>
                    {signedIn ? (plan.code === 'free' ? 'Open your feed' : `Choose ${plan.name}`) : plan.code === 'free' ? 'Create free account' : 'Start free trial'}
                  </Link>
                )}
              />
            ) : (
              <div className="plan-grid" aria-busy="true">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="plan-card skeleton" style={{ height: 420 }} />
                ))}
              </div>
            )}
            <p className="plans-note">Prices are per month in Indian rupees. Pay by UPI Autopay, card or net banking through Razorpay.</p>
          </div>
        </section>
        )}

        <section id="faq" className="section">
          <div className="landing-container faq-wrap">
            <div className="section-head">
              <span className="kicker">FAQ</span>
              <h2>Questions, answered</h2>
            </div>
            <div className="faq">
              {FAQ.filter((f) => features.billing || f.q !== 'What does it cost?').map((item) => (
                <details key={item.q}>
                  <summary>{item.q}</summary>
                  <p>{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="final-cta">
          <div className="landing-container final-inner">
            <h2>Stop scrolling headlines. Start reading signal.</h2>
            <p>Set up in under a minute: your feed is ready the moment you sign up.</p>
            <Link to={signedIn ? '/feed' : '/signup'} className="btn btn-primary btn-lg">
              {signedIn ? 'Open your feed' : 'Get started free'} <ArrowRight size={18} />
            </Link>
          </div>
        </section>
      </main>

      <SiteFooter />
    </div>
  )
}

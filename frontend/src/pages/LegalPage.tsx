import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Logo } from '../components/Logo'
import { SiteFooter } from '../components/SiteFooter'
import '../styles/auth.css'

const UPDATED = '10 October 2026'

function LegalShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="legal-page">
      <header className="legal-top">
        <Link to="/" aria-label="News Intelligence home">
          <Logo size={24} />
        </Link>
        <Link to="/signup" className="auth-back">
          <ArrowLeft size={16} /> Back to sign up
        </Link>
      </header>
      <article className="legal-body">
        <p className="draft-banner">
          Draft for the beta. This text has not yet been reviewed by a lawyer and will be replaced before public launch.
        </p>
        <h1>{title}</h1>
        <p className="muted">Last updated {UPDATED}</p>
        {children}
      </article>
      <SiteFooter />
    </div>
  )
}

export function TermsPage() {
  return (
    <LegalShell title="Terms of Service">
      <h2>1. What the service is</h2>
      <p>
        News Intelligence collects headlines from public and official news sources, groups reports of the same event, and
        ranks them for you according to the rules in your profiles. Alerts tell you when a story crosses the line you set.
      </p>
      <h2>2. Not investment advice</h2>
      <p>
        We rank news. We don’t recommend buying or selling anything, and nothing in the service is financial, legal or tax
        advice. Scores and AI-written text can be wrong or late. Always check the original source before acting.
      </p>
      <h2>3. Your account</h2>
      <p>
        Give accurate details, keep your password private, and tell us if you think someone else has used your account. One
        person per account. You can delete your account at any time from My account.
      </p>
      <h2>4. Content from publishers</h2>
      <p>
        Headlines and links belong to their publishers. We show the headline, the source and a link; read the full story on
        the publisher’s site. Don’t copy or resell content from the service.
      </p>
      <h2>5. Acceptable use</h2>
      <p>No scraping, automated bulk access, attempts to break security, or use that harms other people or the service.</p>
      <h2>6. Availability</h2>
      <p>
        During the beta the service is free and provided as is. Sources can be late or unavailable, and features may change.
      </p>
      <h2>7. Changes</h2>
      <p>If these terms change in a way that matters, we’ll tell you in the app before the change applies.</p>
    </LegalShell>
  )
}

export function PrivacyPage() {
  return (
    <LegalShell title="Privacy Policy">
      <h2>What we store</h2>
      <ul>
        <li>
          <strong>Account:</strong> your name, email, phone number and a one-way hash of your password (never the password
          itself).
        </li>
        <li>
          <strong>Your settings:</strong> profiles and their rules, notification preferences, quiet hours.
        </li>
        <li>
          <strong>Your activity in the app:</strong> stories you read or save, relevance feedback, alerts sent to you.
        </li>
        <li>
          <strong>Telegram</strong> (only if you connect it): your chat id and Telegram username.
        </li>
        <li>
          <strong>Security:</strong> signed-in devices (browser type, IP address, last active) and when you accepted these
          terms.
        </li>
      </ul>
      <h2>Why</h2>
      <p>
        To run your account, rank news for you, deliver alerts, keep your account secure, and (only if you opt in) send
        occasional product updates. We don’t sell your data and don’t show ads.
      </p>
      <h2>Who else sees it</h2>
      <p>
        Our hosting provider stores the data. Telegram delivers your alerts if you connect it. Administrators can see account
        details (name, email, phone, sign-up and sign-in dates) to run and support the service; they can’t see your password.
      </p>
      <h2>How long we keep it</h2>
      <p>
        As long as your account exists. When you delete your account, your data is deleted straight away (backups expire
        within 30 days).
      </p>
      <h2>Your rights</h2>
      <p>
        You can see and correct your details in My account, and delete your account there at any time. Under India’s Digital
        Personal Data Protection Act you can also ask us what we hold about you or raise a grievance with us.
      </p>
    </LegalShell>
  )
}

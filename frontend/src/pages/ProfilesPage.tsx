import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, FilePlus2, LayoutTemplate, SlidersHorizontal } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { ApiError } from '../api/client'
import * as endpoints from '../api/endpoints'
import { relativeTime } from '../lib/time'

export function ProfilesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const profiles = useQuery({ queryKey: ['profiles'], queryFn: endpoints.profiles })
  const templates = useQuery({ queryKey: ['templates'], queryFn: endpoints.templates, staleTime: Infinity })

  const create = useMutation({
    mutationFn: endpoints.createProfile,
    onSuccess: async (profile) => {
      await queryClient.invalidateQueries({ queryKey: ['profiles'] })
      navigate(`/profiles/${profile.id}`)
    },
  })

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">What matters to you</p>
          <h1>Profiles</h1>
          <p className="page-sub">Each profile is its own ranked feed, with its own rules and alert line.</p>
        </div>
      </header>

      <div className="profile-grid">
        {profiles.data?.map((p) => (
          <Link key={p.id} to={`/profiles/${p.id}`} className="profile-card">
            <div className="profile-card-head">
              <span className="profile-card-icon">
                <SlidersHorizontal size={18} />
              </span>
              <strong>{p.name}</strong>
            </div>
            <div className="profile-meta">
              <span className="chip">{p.rule_count} rules</span>
              <span className="chip">Show {p.show_threshold}+</span>
              <span className="chip">Alert {p.alert_threshold}+</span>
            </div>
            <span className="muted small">
              Edited {relativeTime(p.updated_at)} · <span className="profile-link">Edit</span> <ArrowRight size={12} style={{ display: 'inline' }} />
            </span>
          </Link>
        ))}
      </div>

      <h2 className="section-title">Add a profile</h2>
      <div className="option-grid">
        {templates.data?.map((t) => (
          <button
            key={t.slug}
            type="button"
            className="option-card"
            disabled={create.isPending}
            onClick={() => create.mutate({ template: t.slug, name: `${t.name} (copy)` })}
          >
            <span className="option-icon">
              <LayoutTemplate size={18} />
            </span>
            <strong>Start from “{t.name}”</strong>
            <span>{t.description}</span>
          </button>
        ))}
        <button
          type="button"
          className="option-card"
          disabled={create.isPending}
          onClick={() => create.mutate({ name: 'My profile', rules: [] })}
        >
          <span className="option-icon">
            <FilePlus2 size={18} />
          </span>
          <strong>Start empty</strong>
          <span>Pick your own companies, topics and keywords from scratch.</span>
        </button>
      </div>
      {create.error && (
        <p className="error-text" style={{ marginTop: 12 }}>
          {create.error instanceof ApiError ? create.error.message : 'Could not create the profile.'}
        </p>
      )}
    </div>
  )
}

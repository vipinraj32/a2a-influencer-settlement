import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { api } from "../../api";
import { Logo } from "../../components/Logo";
import { useSession } from "../../session";
import { Cta, Icon, Status, StatusTag } from "./parts";
import "./landing.css";

const REPO = "https://github.com/vipinraj32/a2a-influencer-settlement";
const DOCS = `${REPO}#readme`;
const IMG = "/landing";

const NAV = [
  { href: "#product", label: "Product" },
  { href: "#how", label: "How It Works" },
  { href: "#creators", label: "For Creators" },
  { href: "#technology", label: "Technology" },
  { href: "#resources", label: "Resources" },
];

function Header({ signedIn, advertiserStart, influencerStart }: { signedIn: boolean; advertiserStart: string; influencerStart: string }) {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className={`lp-header ${scrolled ? "lp-scrolled" : ""}`}>
      <div className="lp-wide lp-header-row">
        <a href="#top" className="lp-brand" aria-label="ZyroXQ home">
          <Logo size={30} />
        </a>
        <nav className={`lp-nav ${open ? "lp-open" : ""}`} id="lp-nav" aria-label="Main">
          <ul>
            {NAV.map((n) => (
              <li key={n.href}>
                <a href={n.href} onClick={() => setOpen(false)}>
                  {n.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="lp-nav-actions">
            <Link className="lp-signin" to={signedIn ? "/app" : "/login"}>
              {signedIn ? "Open app" : "Log In"}
            </Link>
            {!signedIn && (
              <Cta to={influencerStart} variant="outline">
                Join as a Creator
              </Cta>
            )}
            <Cta to={advertiserStart}>Launch a Campaign</Cta>
          </div>
        </nav>
        <button type="button" className="lp-menu-btn" aria-expanded={open} aria-controls="lp-nav" onClick={() => setOpen(!open)}>
          <Icon name={open ? "close" : "menu"} size={22} />
          <span className="lp-sr">{open ? "Close menu" : "Open menu"}</span>
        </button>
      </div>
    </header>
  );
}

const SLIDES = [
  {
    img: "hero-agents.webp",
    alt: "ZyroXQ campaign dashboard: five creators at different negotiation rounds, sample data",
    eyebrow: "Agent negotiation",
    title: "Your agent talks to every creator’s agent",
    text: "Offers and counters move over A2A in parallel, and every step stays inside the budget, ceilings and rounds you set.",
    link: "#negotiate",
    linkLabel: "See how it negotiates",
  },
  {
    img: "hero-proof.webp",
    alt: "ZyroXQ content agent report: brand mentions found in captions and logo visible on screen, sample data",
    eyebrow: "Content agent",
    title: "The video is checked before anyone is paid",
    text: "Brand said 4 times in the creator’s voice? Logo on screen? Disclosure in the description? The content agent reads it from the video.",
    link: "#verify",
    linkLabel: "See the checks",
  },
  {
    img: "hero-escrow.webp",
    alt: "ZyroXQ escrow view: payment mandate, payout split and deal timeline, sample testnet data",
    eyebrow: "Escrow payouts · testnet",
    title: "Funds wait in escrow until the proof is in",
    text: "Your wallet signs the Payment Mandate. The fixed fee is released on verified proof, performance at T+7 days, and the rest is refunded.",
    link: "#technology",
    linkLabel: "What is live today",
  },
];

function HeroCarousel() {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const n = SLIDES.length;
  const go = useCallback((d: number) => setI((v) => (v + d + n) % n), [n]);

  useEffect(() => {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (paused || still) return;
    const t = window.setTimeout(() => go(1), 7000);
    return () => window.clearTimeout(t);
  }, [i, paused, go]);

  return (
    <div
      className="lp-carousel"
      role="region"
      aria-roledescription="carousel"
      aria-label="ZyroXQ highlights"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      {SLIDES.map((s, k) => (
        <div key={s.img} className={`lp-slide ${k === i ? "lp-on" : ""}`} role="group" aria-roledescription="slide" aria-label={`${k + 1} of ${n}`} aria-hidden={k !== i}>
          <img src={`${IMG}/${s.img}`} alt={s.alt} loading={k === 0 ? "eager" : "lazy"} />
          <div className="lp-slide-copy">
            <p className="lp-slide-eyebrow">{s.eyebrow}</p>
            <h2>{s.title}</h2>
            <p>{s.text}</p>
            <a href={s.link} tabIndex={k === i ? 0 : -1}>
              {s.linkLabel}
            </a>
          </div>
        </div>
      ))}
      <div className="lp-carousel-ui">
        <div className="lp-progress" aria-hidden>
          {SLIDES.map((s, k) => (
            <span key={s.img} className={k === i ? "lp-on" : ""} />
          ))}
        </div>
        <div className="lp-arrows">
          <button type="button" onClick={() => go(-1)} aria-label="Previous slide">
            <Icon name="arrowLeft" size={26} />
          </button>
          <button type="button" onClick={() => go(1)} aria-label="Next slide">
            <Icon name="arrow" size={26} />
          </button>
        </div>
      </div>
      <p className="lp-sample-tag">Illustrative previews · sample data</p>
    </div>
  );
}

const STEPS = [
  { title: "Set your limits", text: "Budget, max fee, max CPM, deliverable and the content checks the video must pass." },
  { title: "Agents negotiate", text: "Your agent finds creators by niche and negotiates with their agents over A2A." },
  { title: "You sign once", text: "Rules check the agreed terms; your browser wallet signs the Payment Mandate." },
  { title: "Proof releases pay", text: "The verifier checks the post. Escrow pays the creator and refunds the rest." },
];

const FEATURES: { id: string; eyebrow: string; title: string; text: string; img: string; alt: string; status: Status; note?: string }[] = [
  {
    id: "negotiate",
    eyebrow: "Negotiate",
    title: "Let agents agree on the deal, inside your rules",
    text: "Your advertiser agent discovers creators through their Agent Cards and negotiates fixed fees, per-1K-view rates and caps in parallel. A deterministic policy engine decides every accept or counter. The model only suggests and explains.",
    img: "feat-negotiate.webp",
    alt: "A2A negotiation thread next to your limits and the policy decision, sample data",
    status: "built",
  },
  {
    id: "verify",
    eyebrow: "Verify",
    title: "Know why a video passed — or why it didn’t",
    text: "The verifier checks channel ownership, publish window, length and disclosure through the YouTube Data API. The content agent counts brand mentions in captions and looks for your logo on screen. A rejected creator sees exactly what to fix and can resubmit before the deadline.",
    img: "feat-verify.webp",
    alt: "Rejected proof with the reasons and fixes shown to the creator, sample data",
    status: "beta",
    note: "logo detection is advisory and goes to advertiser review",
  },
  {
    id: "measure",
    eyebrow: "Measure",
    title: "See where every dollar went",
    text: "Track what is locked in escrow, what was paid for verified content and what came back to you. Views come from the platform API, never from the creator’s own report.",
    img: "feat-measure.webp",
    alt: "Campaign results with escrow totals and a views-per-day chart, sample data",
    status: "beta",
  },
  {
    id: "connect",
    eyebrow: "Connect",
    title: "Infrastructure other agents can run on",
    text: "Every creator agent publishes an A2A Agent Card. Channel data is exposed as MCP tools, so your own agents can search creators and read channel details. Rate cards stay private to the creator.",
    img: "feat-scale.webp",
    alt: "Creator Agent Card and the MCP tools list, sample data",
    status: "built",
  },
];

const TECH: { name: string; role: string; status: Status; note?: string }[] = [
  { name: "A2A protocol", role: "Agent Cards & offers", status: "built" },
  { name: "MCP", role: "Creator & channel tools", status: "built" },
  { name: "LangGraph", role: "Agent workflows", status: "built" },
  { name: "YouTube Data API", role: "Proof & channel checks", status: "built" },
  { name: "Google sign-in", role: "Accounts & channel ownership", status: "built" },
  { name: "Claude", role: "Explanations & vision checks", status: "built" },
  { name: "PostgreSQL", role: "Deals, policies, audit log", status: "built" },
  { name: "HashKey Payment (HSP)", role: "Cart & Payment Mandates", status: "integrating", note: "simulated until hosted access is confirmed" },
  { name: "HashKey Chain", role: "Escrow contract, chain 133", status: "integrating", note: "testnet; contract not yet deployed" },
  { name: "USDC · EIP-3009", role: "Signed transfer authorizations", status: "integrating", note: "testnet only" },
];

const RESOURCES = [
  { img: "res-escrow.webp", kind: "Guide", title: "How escrow-backed payouts work", text: "Funding evidence, the fixed and performance split, refunds and what happens when a payment is unclear." },
  { img: "res-a2a.webp", kind: "Architecture", title: "Agent Cards and A2A negotiation", text: "How advertiser and creator agents find each other, exchange offers and sign terms." },
  { img: "res-content.webp", kind: "Product", title: "What the content agent checks in a video", text: "Spoken brand mentions from captions, logo frames, disclosures — and how results are reviewed." },
];

function Resources() {
  const track = useRef<HTMLUListElement>(null);
  const scroll = (d: number) => {
    const el = track.current;
    if (!el) return;
    const card = el.querySelector("li");
    el.scrollBy({ left: d * ((card?.clientWidth ?? 400) + 32), behavior: "smooth" });
  };
  return (
    <section className="lp-resources" id="resources" aria-labelledby="res-title">
      <div className="lp-container">
        <div className="lp-res-head">
          <div>
            <h2 id="res-title">Learn how ZyroXQ works under the hood</h2>
            <p>Plain-language guides to the agents, the rules and the payment flow — straight from the project documentation.</p>
            <Cta to={DOCS} size="lg">
              Read the documentation
            </Cta>
          </div>
          <div className="lp-arrows lp-arrows-dark">
            <button type="button" onClick={() => scroll(-1)} aria-label="Previous guides">
              <Icon name="arrowLeft" size={26} />
            </button>
            <button type="button" onClick={() => scroll(1)} aria-label="Next guides">
              <Icon name="arrow" size={26} />
            </button>
          </div>
        </div>
        <ul className="lp-res-track" ref={track}>
          {RESOURCES.map((r) => (
            <li key={r.title}>
              <a href={DOCS} target="_blank" rel="noreferrer" className="lp-res-card">
                <img src={`${IMG}/${r.img}`} alt="" loading="lazy" />
                <span className="lp-res-body">
                  <small>{r.kind}</small>
                  <strong>{r.title}</strong>
                  <span>{r.text}</span>
                </span>
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

export function Landing() {
  const { me } = useSession();
  const signedIn = !!me;
  const advertiserStart = signedIn ? "/app" : api.loginUrl("advertiser");
  const influencerStart = signedIn ? "/app" : api.loginUrl("influencer");

  useEffect(() => {
    document.title = "ZyroXQ — Influencer marketing, powered by AI agents";
  }, []);

  return (
    <div className="lp" id="top">
      <a className="lp-skip" href="#main">
        Skip to content
      </a>
      <Header signedIn={signedIn} advertiserStart={advertiserStart} influencerStart={influencerStart} />

      <main id="main">
        {/* Hero: copy on the left, product carousel on the right */}
        <section className="lp-hero" aria-labelledby="hero-title">
          <div className="lp-hero-copy">
            <h1 id="hero-title">
              Your agents negotiate. <span className="lp-hl">Your rules decide.</span>
            </h1>
            <p className="lp-hero-strong">
              Your limits. Your wallet. Your verified results.
              <br />
              All in one agent-driven deal flow.
            </p>
            <p className="lp-hero-lead">
              ZyroXQ lets AI agents discover creators and negotiate sponsorships for you, then holds the payment in escrow until the content is verified. The AI
              proposes, deterministic rules decide, and only your wallet signs.
            </p>
            <div className="lp-cta-row">
              <Cta to={advertiserStart} size="lg">
                Launch a Campaign
              </Cta>
              <Cta to="#how" variant="outline" size="lg">
                Explore How It Works
              </Cta>
            </div>
          </div>
          <HeroCarousel />
        </section>

        {/* Core rule card */}
        <section className="lp-rule" aria-labelledby="rule-title">
          <div className="lp-container">
            <div className="lp-rule-card">
              <p className="lp-rule-label">The ZyroXQ core rule</p>
              <div className="lp-rule-grid">
                <div>
                  <blockquote>
                    <p id="rule-title">The AI proposes. Deterministic rules decide. Authorized code signs.</p>
                  </blockquote>
                  <p className="lp-rule-sub">No payment ever depends on model output alone. Signing is never an AI tool.</p>
                  <Cta to={DOCS} size="lg">
                    Read the design
                  </Cta>
                </div>
                <div className="lp-rule-art">
                  <img src={`${IMG}/principle.webp`} alt="Diagram: the AI proposes, deterministic rules decide, authorized code signs" loading="lazy" />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Intro statement */}
        <section className="lp-band" aria-labelledby="intro-title">
          <div className="lp-container lp-narrow">
            <h2 id="intro-title">Turn every sponsorship into a deal you can verify</h2>
            <p>
              Creator deals still run on DMs, spreadsheets and trust. Rates get haggled by hand, posts are checked by eye, and payment is either sent too early or
              chased for weeks.
            </p>
            <p className="lp-strong">
              ZyroXQ gives advertisers and creators agents that agree on clear terms, a verifier that checks the published content, and an escrow that pays only
              for what was delivered.
            </p>
            <Cta to={advertiserStart} size="lg">
              Launch a Campaign
            </Cta>
          </div>
        </section>

        {/* Creator split card */}
        <section className="lp-band lp-band-tight" id="creators" aria-labelledby="creators-title">
          <div className="lp-container">
            <div className="lp-split">
              <img src={`${IMG}/creator.webp`} alt="Creator agent profile with niches, a private rate card and the agent inbox, sample data" loading="lazy" />
              <div className="lp-split-copy">
                <p className="lp-eyebrow">For creators</p>
                <p className="lp-split-quote" id="creators-title">
                  Your agent negotiates above your private floor — and you see every term before anything is signed.
                </p>
                <ul className="lp-split-list">
                  <li>Niches suggested from your YouTube channel, editable any time</li>
                  <li>Your rate card never leaves your side</li>
                  <li>Clear reasons and a resubmit window if a video is rejected</li>
                </ul>
                <Cta to={influencerStart} variant="dark">
                  Join as a Creator
                </Cta>
              </div>
            </div>
          </div>
        </section>

        {/* Your terms + steps */}
        <section className="lp-band" id="how" aria-labelledby="how-title">
          <div className="lp-container">
            <div className="lp-narrow">
              <h2 id="how-title">Your terms. Your limits. Your keys.</h2>
              <p className="lp-strong">One deal flow, with you in control at every step.</p>
              <p>
                You decide what a campaign may spend and what the video must contain. Agents do the back-and-forth, rules enforce your limits, and nothing moves
                without your signature.
              </p>
            </div>
            <ol className="lp-steps">
              {STEPS.map((s, k) => (
                <li key={s.title}>
                  <span className="lp-step-n" aria-hidden>
                    {String(k + 1).padStart(2, "0")}
                  </span>
                  <h3>
                    <span className="lp-sr">Step {k + 1}: </span>
                    {s.title}
                  </h3>
                  <p>{s.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Feature rows */}
        <section className="lp-band lp-features" id="product" aria-label="Product">
          <div className="lp-container">
            {FEATURES.map((f, k) => (
              <article key={f.id} id={f.id} className={`lp-feature ${k % 2 ? "lp-flip" : ""}`} aria-labelledby={`${f.id}-title`}>
                <div className="lp-feature-copy">
                  <p className="lp-feature-eyebrow">
                    {f.eyebrow} <StatusTag status={f.status} note={f.note} />
                  </p>
                  <h3 id={`${f.id}-title`}>{f.title}</h3>
                  <p>{f.text}</p>
                  {f.note && <p className="lp-note">Note: {f.note}.</p>}
                  <a className="lp-more" href={DOCS} target="_blank" rel="noreferrer">
                    Learn more <Icon name="arrow" size={16} />
                  </a>
                </div>
                <figure className="lp-feature-img">
                  <img src={`${IMG}/${f.img}`} alt={f.alt} loading="lazy" />
                  <figcaption>Illustrative preview · sample data</figcaption>
                </figure>
              </article>
            ))}
          </div>
        </section>

        {/* Technology grid */}
        <section className="lp-tech" id="technology" aria-labelledby="tech-title">
          <div className="lp-container">
            <p className="lp-eyebrow">Open standards</p>
            <h2 id="tech-title">Creator deal technology you can inspect</h2>
            <p className="lp-tech-lead">
              ZyroXQ is built on open protocols and public APIs. Here is exactly what works today and what is still being integrated — this is a testnet project and
              no real funds move.
            </p>
            <ul className="lp-tech-grid">
              {TECH.map((t) => (
                <li key={t.name}>
                  <strong>{t.name}</strong>
                  <span>{t.role}</span>
                  <StatusTag status={t.status} note={t.note} />
                  {t.note && <small>{t.note}</small>}
                </li>
              ))}
            </ul>
          </div>
        </section>

        <Resources />
      </main>

      <footer className="lp-footer">
        <div className="lp-wide">
          <div className="lp-footer-top">
            <Logo size={34} />
            <a className="lp-social" href={REPO} target="_blank" rel="noreferrer" aria-label="ZyroXQ on GitHub">
              <Icon name="github" size={26} />
            </a>
          </div>
          <div className="lp-footer-cols">
            <nav aria-label="Product">
              <h2>Product</h2>
              <ul>
                <li>
                  <a href="#negotiate">Agent negotiation</a>
                </li>
                <li>
                  <a href="#verify">Content verification</a>
                </li>
                <li>
                  <a href="#measure">Campaign results</a>
                </li>
                <li>
                  <a href="#connect">Agent Cards &amp; MCP</a>
                </li>
                <li>
                  <a href="#technology">What is live today</a>
                </li>
              </ul>
            </nav>
            <nav aria-label="Get started">
              <h2>Get started</h2>
              <ul>
                <li>
                  <a href={advertiserStart}>For advertisers</a>
                </li>
                <li>
                  <a href={influencerStart}>For creators</a>
                </li>
                <li>
                  <a href="#how">How it works</a>
                </li>
                <li>
                  <Link to="/login">Log in</Link>
                </li>
              </ul>
            </nav>
            <nav aria-label="Project">
              <h2>Project</h2>
              <ul>
                <li>
                  <a href={DOCS} target="_blank" rel="noreferrer">
                    Documentation
                  </a>
                </li>
                <li>
                  <a href={REPO} target="_blank" rel="noreferrer">
                    GitHub
                  </a>
                </li>
                <li>
                  <span className="lp-soon">Privacy · coming soon</span>
                </li>
                <li>
                  <span className="lp-soon">Terms · coming soon</span>
                </li>
                <li>
                  <span className="lp-soon">Contact · coming soon</span>
                </li>
              </ul>
            </nav>
            <div className="lp-footer-cta">
              <h2>Build campaigns on trust, not guesswork</h2>
              <p>Sign in with Google as an advertiser or a creator. Your agent is ready in minutes.</p>
              <div className="lp-cta-row">
                <Cta to={advertiserStart}>Get Started</Cta>
                <Cta to={influencerStart} variant="outline">
                  I’m a creator
                </Cta>
              </div>
            </div>
          </div>
          <div className="lp-footer-base">
            <span>© {new Date().getFullYear()} ZyroXQ</span>
            <span>Built for the HashKey “Agent Payments &amp; A2A Settlement” track · testnet</span>
          </div>
        </div>
      </footer>
    </div>
  );
}

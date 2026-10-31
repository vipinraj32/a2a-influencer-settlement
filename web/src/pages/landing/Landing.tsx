import { createContext, useCallback, useContext, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Link } from "react-router";
import { LazyMotion, MotionConfig, domAnimation, m as motion, useMotionValueEvent, useReducedMotion, useScroll, useSpring } from "motion/react";
import { Logo } from "../../components/Logo";
import { useSession } from "../../session";
import { Cta, Icon, Status, StatusTag } from "./parts";
import "./landing.css";

const REPO = "https://github.com/vipinraj32/a2a-influencer-settlement";
const DOCS = `${REPO}#readme`;
const IMG = "/landing";
const EASE = [0.2, 0.7, 0.2, 1] as const;

const NAV = [
  { href: "#product", label: "Product" },
  { href: "#how", label: "How It Works" },
  { href: "#payouts", label: "Payouts" },
  { href: "#creators", label: "For Creators" },
  { href: "#faq", label: "FAQ" },
];

type Direction = "down" | "up";

/** Which way the page is scrolling; sections enter from that side. */
const ScrollDirection = createContext<Direction>("down");

/** Props for an element that slides in from the scroll direction each time it enters the viewport. */
function revealProps(dir: Direction, delay = 0) {
  return {
    initial: "hidden",
    whileInView: "show",
    viewport: { once: false, amount: 0.2 },
    variants: {
      hidden: { opacity: 0, y: dir === "down" ? 48 : -48 },
      show: { opacity: 1, y: 0, transition: { duration: 0.6, delay, ease: EASE } },
    },
  } as const;
}

function useReveal() {
  const dir = useContext(ScrollDirection);
  return (delay = 0) => revealProps(dir, delay);
}

function Header({ direction, signedIn, advertiserStart, influencerStart }: { direction: Direction; signedIn: boolean; advertiserStart: string; influencerStart: string }) {
  const [open, setOpen] = useState(false);
  const [atTop, setAtTop] = useState(true);
  const [active, setActive] = useState("");
  const { scrollY, scrollYProgress } = useScroll();
  const progress = useSpring(scrollYProgress, { stiffness: 220, damping: 32, restDelta: 0.001 });

  useMotionValueEvent(scrollY, "change", (current) => {
    setAtTop(current < 80);
    const line = window.innerHeight * 0.35;
    let found = "";
    for (const n of NAV) {
      const r = document.querySelector(n.href)?.getBoundingClientRect();
      if (r && r.top <= line && r.bottom > line) found = n.href;
    }
    setActive(found);
  });
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Hide while reading down the page, come back as soon as the visitor scrolls up.
  const hidden = direction === "down" && !atTop && !open;

  return (
    <motion.header className={`lp-header ${atTop ? "" : "lp-scrolled"}`} animate={{ y: hidden ? "-100%" : "0%" }} transition={{ duration: 0.35, ease: EASE }}>
      <div className="lp-wide lp-header-row">
        <a href="#top" className="lp-brand" aria-label="ZyroXQ home">
          <Logo size={34} />
        </a>
        <nav className={`lp-nav ${open ? "lp-open" : ""}`} id="lp-nav" aria-label="Main">
          <ul>
            {NAV.map((n) => (
              <li key={n.href}>
                <a href={n.href} className={active === n.href ? "lp-current" : ""} aria-current={active === n.href ? "location" : undefined} onClick={() => setOpen(false)}>
                  {n.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="lp-nav-actions">
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
      <motion.span className="lp-scrollbar" style={{ scaleX: progress }} aria-hidden />
    </motion.header>
  );
}

const SLIDES = [
  { img: "hero-agents.webp", alt: "Campaign dashboard with creators at different negotiation rounds, sample data", eyebrow: "Agent negotiation", title: "Your agent talks to every creator’s agent", link: "#negotiate" },
  { img: "hero-proof.webp", alt: "Content agent report on brand mentions and logo visibility, sample data", eyebrow: "Content agent", title: "Every video is checked before payout", link: "#verify" },
  { img: "hero-escrow.webp", alt: "Escrow view with payment mandate and payout split, sample testnet data", eyebrow: "Escrow · testnet", title: "Funds wait in escrow until proof is in", link: "#payouts" },
];

const SLIDE_MS = 6000;

function HeroCarousel() {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const touch = useRef<number | null>(null);
  const still = useReducedMotion();
  const n = SLIDES.length;
  const go = useCallback((d: number) => setI((v) => (v + d + n) % n), [n]);

  useEffect(() => {
    if (paused || still) return;
    const t = window.setTimeout(() => go(1), SLIDE_MS);
    return () => window.clearTimeout(t);
  }, [i, paused, go, still]);

  return (
    <div
      className={`lp-carousel ${paused || still ? "lp-paused" : ""}`}
      role="region"
      aria-roledescription="carousel"
      aria-label="ZyroXQ highlights"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") go(-1);
        if (e.key === "ArrowRight") go(1);
      }}
      onTouchStart={(e) => (touch.current = e.touches[0].clientX)}
      onTouchEnd={(e) => {
        if (touch.current === null) return;
        const dx = e.changedTouches[0].clientX - touch.current;
        if (Math.abs(dx) > 40) go(dx < 0 ? 1 : -1);
        touch.current = null;
      }}
    >
      {SLIDES.map((s, k) => (
        <div key={s.img} className={`lp-slide ${k === i ? "lp-on" : ""}`} role="group" aria-roledescription="slide" aria-label={`${k + 1} of ${n}`} aria-hidden={k !== i}>
          <img src={`${IMG}/${s.img}`} alt={s.alt} loading={k === 0 ? "eager" : "lazy"} />
          <div className="lp-slide-copy">
            <p className="lp-slide-eyebrow">{s.eyebrow}</p>
            <h2>{s.title}</h2>
            <a href={s.link} tabIndex={k === i ? 0 : -1}>
              See more <Icon name="arrow" size={16} />
            </a>
          </div>
        </div>
      ))}
      <div className="lp-carousel-ui">
        <div className="lp-progress">
          {SLIDES.map((s, k) => (
            <button key={s.img} type="button" className={k === i ? "lp-on" : k < i ? "lp-done" : ""} onClick={() => setI(k)} aria-label={`Show slide ${k + 1}: ${s.eyebrow}`} aria-current={k === i}>
              <span key={k === i ? `on-${i}` : "off"} style={{ animationDuration: `${SLIDE_MS}ms` }} />
            </button>
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
      <p className="lp-sample-tag">Sample data</p>
    </div>
  );
}

const STEPS = [
  { icon: "key", title: "Set your limits", detail: "Budget, max fee, max CPM and the checks a video must pass.", points: ["Budget reserved up front", "Fee and CPM ceilings", "Content checks"] },
  { icon: "agents", title: "Agents negotiate", detail: "Your agent sends offers to creator agents over A2A. Rules decide every reply.", points: ["Parallel offers", "Round limits", "Floors stay private"] },
  { icon: "shield", title: "You sign once", detail: "Terms are re-checked, then your wallet signs the Payment Mandate.", points: ["Policy check first", "EIP-712 in your wallet", "AI never signs"] },
  { icon: "eye", title: "Proof releases pay", detail: "The post is verified, then escrow pays out and refunds the rest.", points: ["YouTube checks", "Fixed, then performance", "Unused budget refunded"] },
];

function Steps() {
  const [cur, setCur] = useState(0);
  const [auto, setAuto] = useState(true);
  const [visible, setVisible] = useState(false);
  const still = useReducedMotion();
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const reveal = useReveal();

  useEffect(() => {
    if (!auto || !visible || still) return;
    const t = window.setTimeout(() => setCur((c) => (c + 1) % STEPS.length), 4500);
    return () => window.clearTimeout(t);
  }, [cur, auto, visible, still]);

  const pick = (k: number, focus = false) => {
    setAuto(false);
    setCur(k);
    if (focus) tabs.current[k]?.focus();
  };
  const onKey = (e: ReactKeyboardEvent) => {
    const map: Record<string, number> = { ArrowRight: cur + 1, ArrowDown: cur + 1, ArrowLeft: cur - 1, ArrowUp: cur - 1, Home: 0, End: STEPS.length - 1 };
    if (!(e.key in map)) return;
    e.preventDefault();
    pick((map[e.key] + STEPS.length) % STEPS.length, true);
  };
  const s = STEPS[cur];
  const fill = cur / (STEPS.length - 1);

  return (
    <motion.div className="lp-stepper" {...reveal(0.1)} onViewportEnter={() => setVisible(true)} onViewportLeave={() => setVisible(false)}>
      <div className="lp-step-tabs" role="tablist" aria-label="Deal flow steps" onKeyDown={onKey}>
        <span className="lp-step-line" aria-hidden>
          <span style={{ transform: `scaleX(${fill})`, "--s": fill } as CSSProperties} />
        </span>
        {STEPS.map((st, k) => (
          <button
            key={st.title}
            ref={(el) => {
              tabs.current[k] = el;
            }}
            type="button"
            role="tab"
            id={`lp-step-${k}`}
            aria-selected={k === cur}
            aria-controls="lp-step-panel"
            tabIndex={k === cur ? 0 : -1}
            className={`lp-step-tab ${k === cur ? "lp-on" : ""} ${k < cur ? "lp-done" : ""}`}
            onClick={() => pick(k)}
          >
            <span className="lp-step-n">{String(k + 1).padStart(2, "0")}</span>
            <span className="lp-step-t">{st.title}</span>
          </button>
        ))}
      </div>
      <motion.div
        className="lp-step-panel"
        role="tabpanel"
        id="lp-step-panel"
        aria-labelledby={`lp-step-${cur}`}
        key={cur}
        initial={{ opacity: 0, x: 24 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.4, ease: EASE }}
      >
        <span className="lp-step-ic" aria-hidden>
          <Icon name={s.icon} size={30} />
        </span>
        <div>
          <h3>{s.title}</h3>
          <p>{s.detail}</p>
        </div>
        <ul>
          {s.points.map((p) => (
            <li key={p}>
              <Icon name="check" size={18} />
              {p}
            </li>
          ))}
        </ul>
        <div className="lp-step-nav">
          <button type="button" onClick={() => pick((cur - 1 + STEPS.length) % STEPS.length)} aria-label="Previous step">
            <Icon name="arrowLeft" size={20} />
          </button>
          <button type="button" onClick={() => pick((cur + 1) % STEPS.length)} aria-label="Next step">
            <Icon name="arrow" size={20} />
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

/** Same formula as agent_policy/pricing.py (hybrid): fixed + min(cap − fixed, views/1000 × CPM), rounded down to the cent. */
function payout(fixedUsd: number, cpmUsd: number, capUsd: number, views: number) {
  const cents = (v: number) => Math.round(v * 100);
  const cap = cents(capUsd);
  const fixed = Math.min(cents(fixedUsd), cap);
  const variable = Math.floor((views * cents(cpmUsd)) / 1000);
  const performance = Math.max(0, Math.min(cap - fixed, variable));
  return { fixed, performance, refund: cap - fixed - performance, cap, capped: variable > performance };
}

const usd = (c: number) => `$${(c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

type RangeProps = { id: string; label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; format: (v: number) => string };

function Range({ id, label, value, min, max, step, onChange, format }: RangeProps) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="lp-range">
      <label htmlFor={id}>
        <span>{label}</span>
        <output htmlFor={id}>{format(value)}</output>
      </label>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ "--p": `${pct}%` } as CSSProperties} />
    </div>
  );
}

function PayoutCalculator() {
  const [fixed, setFixed] = useState(19);
  const [cpm, setCpm] = useState(2.5);
  const [cap, setCap] = useState(40);
  const [views, setViews] = useState(8000);
  const reveal = useReveal();
  const r = payout(fixed, cpm, cap, views);
  const w = (c: number) => `${r.cap ? (c / r.cap) * 100 : 0}%`;

  return (
    <section className="lp-payouts" id="payouts" aria-labelledby="payouts-title">
      <div className="lp-container">
        <motion.div className="lp-payouts-head" {...reveal()}>
          <p className="lp-eyebrow">Payout math</p>
          <h2 id="payouts-title">See where the money goes</h2>
          <p>Move the sliders to see how a deal pays out. The fixed fee is released on verified proof, the per-1K-view fee after the measuring window, and the rest comes back to you.</p>
        </motion.div>
        <div className="lp-calc">
          <motion.div className="lp-calc-inputs" {...reveal(0.05)}>
            <Range
              id="calc-fixed"
              label="Fixed fee"
              value={fixed}
              min={0}
              max={200}
              step={1}
              format={(v) => `$${v}`}
              onChange={(v) => {
                setFixed(v);
                if (v > cap) setCap(Math.ceil(v / 5) * 5);
              }}
            />
            <Range id="calc-cpm" label="Per 1K views" value={cpm} min={0} max={10} step={0.1} format={(v) => `$${v.toFixed(2)}`} onChange={setCpm} />
            <Range
              id="calc-cap"
              label="Escrow cap"
              value={cap}
              min={10}
              max={500}
              step={5}
              format={(v) => `$${v}`}
              onChange={(v) => {
                setCap(v);
                if (fixed > v) setFixed(v);
              }}
            />
            <Range id="calc-views" label="Verified views" value={views} min={0} max={100000} step={500} format={(v) => v.toLocaleString("en-US")} onChange={setViews} />
          </motion.div>
          <motion.div className="lp-calc-out" aria-live="polite" {...reveal(0.15)}>
            <p className="lp-calc-total">
              <span>Creator is paid</span>
              <strong>{usd(r.fixed + r.performance)}</strong>
              <small>of {usd(r.cap)} in escrow</small>
            </p>
            <div className="lp-stack" role="img" aria-label={`Fixed ${usd(r.fixed)}, performance ${usd(r.performance)}, refunded ${usd(r.refund)}`}>
              <span className="lp-s-fixed" style={{ width: w(r.fixed) }} />
              <span className="lp-s-perf" style={{ width: w(r.performance) }} />
              <span className="lp-s-refund" style={{ width: w(r.refund) }} />
            </div>
            <dl className="lp-legend">
              <div>
                <dt>
                  <i className="lp-s-fixed" /> Fixed
                </dt>
                <dd>{usd(r.fixed)}</dd>
              </div>
              <div>
                <dt>
                  <i className="lp-s-perf" /> Performance
                </dt>
                <dd>{usd(r.performance)}</dd>
                {r.capped && <small>cap reached</small>}
              </div>
              <div>
                <dt>
                  <i className="lp-s-refund" /> Refunded
                </dt>
                <dd>{usd(r.refund)}</dd>
              </div>
            </dl>
            <p className="lp-calc-note">Example numbers · testnet only</p>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

const FEATURES: { id: string; eyebrow: string; title: string; text: string; img: string; alt: string; status: Status; note?: string }[] = [
  {
    id: "negotiate",
    eyebrow: "Negotiate",
    title: "Agents agree on the deal, inside your rules",
    text: "Your agent sends offers to creator agents over A2A, in parallel. A deterministic rules engine decides every accept or counter — the AI only suggests and explains.",
    img: "feat-negotiate.webp",
    alt: "A2A negotiation thread next to your limits and the policy decision, sample data",
    status: "built",
  },
  {
    id: "verify",
    eyebrow: "Verify",
    title: "Know why a video passed — or didn’t",
    text: "The verifier checks the channel, publish window and disclosure through the YouTube Data API, and the content agent looks for brand mentions and your logo. Creators see what to fix and can resubmit.",
    img: "feat-verify.webp",
    alt: "Rejected proof with the reasons and fixes shown to the creator, sample data",
    status: "beta",
    note: "logo detection is advisory",
  },
  {
    id: "measure",
    eyebrow: "Measure",
    title: "See where every dollar went",
    text: "Track what is locked in escrow, what was paid and what was refunded. Views come from the platform API, never from a creator’s own report.",
    img: "feat-measure.webp",
    alt: "Campaign results with escrow totals and a views-per-day chart, sample data",
    status: "beta",
  },
  {
    id: "connect",
    eyebrow: "Connect",
    title: "Built for other agents too",
    text: "Every creator agent publishes an A2A Agent Card, and channel data is available as MCP tools for your own agents. Creator rate cards stay private.",
    img: "feat-scale.webp",
    alt: "Creator Agent Card and the MCP tools list, sample data",
    status: "built",
  },
];

/** Feature screenshot that tilts slightly toward the mouse pointer. */
function TiltFigure({ src, alt }: { src: string; alt: string }) {
  const ref = useRef<HTMLElement>(null);
  const still = useReducedMotion();
  const move = (e: ReactPointerEvent) => {
    const el = ref.current;
    if (!el || e.pointerType !== "mouse" || still) return;
    const b = el.getBoundingClientRect();
    el.style.setProperty("--rx", `${((e.clientY - b.top) / b.height - 0.5) * -6}deg`);
    el.style.setProperty("--ry", `${((e.clientX - b.left) / b.width - 0.5) * 8}deg`);
  };
  const reset = () => {
    ref.current?.style.setProperty("--rx", "0deg");
    ref.current?.style.setProperty("--ry", "0deg");
  };
  return (
    <figure className="lp-feature-img" ref={ref} onPointerMove={move} onPointerLeave={reset}>
      <div className="lp-tilt">
        <img src={src} alt={alt} loading="lazy" />
      </div>
      <figcaption>Sample data</figcaption>
    </figure>
  );
}

function Features() {
  const reveal = useReveal();
  return (
    <section className="lp-band lp-features" id="product" aria-label="Product">
      <div className="lp-container">
        {FEATURES.map((f, k) => (
          <motion.article key={f.id} id={f.id} className={`lp-feature ${k % 2 ? "lp-flip" : ""}`} aria-labelledby={`${f.id}-title`} {...reveal()}>
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
            <TiltFigure src={`${IMG}/${f.img}`} alt={f.alt} />
          </motion.article>
        ))}
      </div>
    </section>
  );
}

const FAQ = [
  { q: "Does real money move today?", a: "No. It runs on HashKey Chain testnet; HSP is simulated and the escrow contract is not deployed yet." },
  { q: "Can the AI spend my budget?", a: "No. Rules decide every offer and only your wallet signs." },
  { q: "Does HSP guarantee the escrow?", a: "No. Release and refund rules live in ZyroXQ’s own escrow contract." },
  { q: "Can advertisers see a creator’s floor?", a: "No. Rate cards never leave the creator’s side." },
];

function Faq() {
  const reveal = useReveal();
  return (
    <section className="lp-faq" id="faq" aria-labelledby="faq-title">
      <div className="lp-container lp-faq-grid">
        <motion.div {...reveal()}>
          <p className="lp-eyebrow">FAQ</p>
          <h2 id="faq-title">Straight answers</h2>
          <p>What ZyroXQ does today, and what is still testnet-only.</p>
          <a className="lp-more" href={DOCS} target="_blank" rel="noreferrer">
            Read the docs <Icon name="arrow" size={16} />
          </a>
        </motion.div>
        <div className="lp-faq-list">
          {FAQ.map((f, k) => (
            <motion.details key={f.q} open={k === 0} {...reveal(k * 0.06)}>
              <summary>
                {f.q}
                <span className="lp-plus" aria-hidden />
              </summary>
              <p>{f.a}</p>
            </motion.details>
          ))}
        </div>
      </div>
    </section>
  );
}

const RESOURCES = [
  { img: "res-escrow.webp", kind: "Guide", title: "How escrow payouts work" },
  { img: "res-a2a.webp", kind: "Architecture", title: "Agent Cards and A2A" },
  { img: "res-content.webp", kind: "Product", title: "What the content agent checks" },
];

function Resources() {
  const track = useRef<HTMLUListElement>(null);
  const [edge, setEdge] = useState({ start: true, end: false });
  const reveal = useReveal();
  const update = useCallback(() => {
    const el = track.current;
    if (el) setEdge({ start: el.scrollLeft < 8, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 8 });
  }, []);
  useEffect(() => {
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [update]);
  const scroll = (d: number) => {
    const el = track.current;
    if (!el) return;
    const card = el.querySelector("li");
    el.scrollBy({ left: d * ((card?.clientWidth ?? 400) + 32), behavior: "smooth" });
  };
  return (
    <section className="lp-resources" id="resources" aria-labelledby="res-title">
      <div className="lp-container">
        <motion.div className="lp-res-head" {...reveal()}>
          <div>
            <h2 id="res-title">Under the hood</h2>
            <p>Guides to the agents, the rules and the payment flow, straight from the project documentation.</p>
            <Cta to={DOCS} size="lg">
              Read the docs
            </Cta>
          </div>
          <div className="lp-arrows">
            <button type="button" onClick={() => scroll(-1)} aria-label="Previous guides" disabled={edge.start}>
              <Icon name="arrowLeft" size={26} />
            </button>
            <button type="button" onClick={() => scroll(1)} aria-label="Next guides" disabled={edge.end}>
              <Icon name="arrow" size={26} />
            </button>
          </div>
        </motion.div>
        <ul className="lp-res-track" ref={track} onScroll={update}>
          {RESOURCES.map((r, k) => (
            <motion.li key={r.title} {...reveal(k * 0.08)}>
              <a href={DOCS} target="_blank" rel="noreferrer" className="lp-res-card">
                <span className="lp-res-img">
                  <img src={`${IMG}/${r.img}`} alt="" loading="lazy" />
                </span>
                <span className="lp-res-body">
                  <small>{r.kind}</small>
                  <strong>{r.title}</strong>
                  <em>
                    Read <Icon name="arrow" size={16} />
                  </em>
                </span>
              </a>
            </motion.li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function BackToTop({ direction }: { direction: Direction }) {
  const { scrollY } = useScroll();
  const [far, setFar] = useState(false);
  useMotionValueEvent(scrollY, "change", (current) => setFar(current > window.innerHeight * 1.2));
  // Offer the shortcut when the visitor starts heading back up.
  const show = far && direction === "up";
  return (
    <a href="#top" className={`lp-top ${show ? "lp-on" : ""}`} aria-label="Back to top" tabIndex={show ? 0 : -1}>
      <Icon name="arrowUp" size={22} />
    </a>
  );
}

export function Landing() {
  const { me } = useSession();
  const signedIn = !!me;
  const advertiserStart = signedIn ? "/app" : "/login?as=advertiser";
  const influencerStart = signedIn ? "/app" : "/login?as=influencer";

  const { scrollY } = useScroll();
  const [scrollDirection, setScrollDirection] = useState<Direction>("down");
  useMotionValueEvent(scrollY, "change", (current) => {
    const diff = current - (scrollY.getPrevious() ?? current);
    if (diff !== 0) setScrollDirection(diff > 0 ? "down" : "up");
  });

  useEffect(() => {
    document.title = "ZyroXQ — Influencer marketing, powered by AI agents";
  }, []);

  const reveal = (delay = 0) => revealProps(scrollDirection, delay);

  return (
    <LazyMotion features={domAnimation} strict>
      <MotionConfig reducedMotion="user">
      <ScrollDirection.Provider value={scrollDirection}>
        <div className="lp" id="top">
          <a className="lp-skip" href="#main">
            Skip to content
          </a>
          <Header direction={scrollDirection} signedIn={signedIn} advertiserStart={advertiserStart} influencerStart={influencerStart} />

          <main id="main">
            {/* Hero */}
            <section className="lp-hero" aria-labelledby="hero-title">
              <motion.div className="lp-hero-copy" initial={{ opacity: 0, y: 32 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, ease: EASE }}>
                <p className="lp-pill">
                  <span className="lp-pill-dot" aria-hidden />
                  HashKey A2A track · testnet
                </p>
                <h1 id="hero-title">
                  Your agents negotiate. <span className="lp-hl">Your rules decide.</span>
                </h1>
                <p className="lp-hero-lead">
                  AI agents find creators and negotiate sponsorships for you. The payment waits in escrow and is released only when the content is verified.
                </p>
                <div className="lp-cta-row">
                  <Cta to={advertiserStart} size="lg">
                    Launch a Campaign
                  </Cta>
                  <Cta to="#how" variant="outline" size="lg">
                    How It Works
                  </Cta>
                </div>
              </motion.div>
              <HeroCarousel />
            </section>

            {/* Core rule */}
            <section className="lp-rule" aria-labelledby="rule-title">
              <div className="lp-container">
                <motion.div className="lp-rule-card" {...reveal()}>
                  <p className="lp-rule-label">The core rule</p>
                  <div className="lp-rule-grid">
                    <div>
                      <blockquote>
                        <p id="rule-title">
                          The AI <em>proposes</em>. Rules <em>decide</em>. Your wallet <em>signs</em>.
                        </p>
                      </blockquote>
                      <p className="lp-rule-sub">No payment ever depends on AI output alone. Signing is never an AI tool.</p>
                      <Cta to={DOCS} size="lg">
                        Read the design
                      </Cta>
                    </div>
                    <div className="lp-rule-art">
                      <img src={`${IMG}/principle.webp`} alt="Diagram: the AI proposes, deterministic rules decide, authorized code signs" loading="lazy" />
                    </div>
                  </div>
                </motion.div>
              </div>
            </section>

            {/* How it works */}
            <section className="lp-band lp-band-alt" id="how" aria-labelledby="how-title">
              <div className="lp-container">
                <motion.div className="lp-narrow" {...reveal()}>
                  <p className="lp-eyebrow">How it works</p>
                  <h2 id="how-title">Your terms. Your limits. Your keys.</h2>
                  <p>Agents do the back-and-forth, rules enforce your limits, and nothing moves without your signature. Pick a step to see what happens.</p>
                </motion.div>
                <Steps />
              </div>
            </section>

            <PayoutCalculator />
            <Features />

            {/* For creators */}
            <section className="lp-band lp-band-tight" id="creators" aria-labelledby="creators-title">
              <div className="lp-container">
                <motion.div className="lp-split" {...reveal()}>
                  <img src={`${IMG}/creator.webp`} alt="Creator agent profile with niches, a private rate card and the agent inbox, sample data" loading="lazy" />
                  <div className="lp-split-copy">
                    <p className="lp-eyebrow">For creators</p>
                    <p className="lp-split-quote" id="creators-title">
                      Your agent negotiates above your private floor.
                    </p>
                    <p className="lp-split-text">Set your rates once. Your agent handles the offers, and you see every term before anything is signed.</p>
                    <ul className="lp-split-list">
                      <li>Niches from your channel</li>
                      <li>Rate card stays private</li>
                      <li>Fix and resubmit if rejected</li>
                    </ul>
                    <Cta to={influencerStart}>Join as a Creator</Cta>
                  </div>
                </motion.div>
              </div>
            </section>

            <Faq />
            <Resources />
          </main>

          <footer className="lp-footer">
            <div className="lp-wide">
              <div className="lp-footer-top">
                <Logo size={38} tone="dark" />
                <a className="lp-social" href={REPO} target="_blank" rel="noreferrer" aria-label="ZyroXQ on GitHub">
                  <Icon name="github" size={26} />
                </a>
              </div>
              <div className="lp-footer-cols">
                <nav aria-label="Product">
                  <h2>Product</h2>
                  <ul>
                    <li>
                      <a href="#negotiate">Negotiation</a>
                    </li>
                    <li>
                      <a href="#verify">Verification</a>
                    </li>
                    <li>
                      <a href="#payouts">Payouts</a>
                    </li>
                  </ul>
                </nav>
                <nav aria-label="Get started">
                  <h2>Get started</h2>
                  <ul>
                    <li>
                      <a href={advertiserStart}>Advertisers</a>
                    </li>
                    <li>
                      <a href={influencerStart}>Creators</a>
                    </li>
                    <li>
                      <a href="#faq">FAQ</a>
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
                        Docs
                      </a>
                    </li>
                    <li>
                      <a href={REPO} target="_blank" rel="noreferrer">
                        GitHub
                      </a>
                    </li>
                    <li>
                      <span className="lp-soon">Privacy · soon</span>
                    </li>
                    <li>
                      <span className="lp-soon">Terms · soon</span>
                    </li>
                  </ul>
                </nav>
                <div className="lp-footer-cta">
                  <h2>Build campaigns on trust</h2>
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
                <span>HashKey A2A Settlement track · testnet</span>
              </div>
            </div>
          </footer>
          <BackToTop direction={scrollDirection} />
        </div>
      </ScrollDirection.Provider>
      </MotionConfig>
    </LazyMotion>
  );
}

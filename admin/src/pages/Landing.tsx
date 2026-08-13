import { Link } from "react-router-dom";

/** Public landing page — what `/` serves to anyone who is not signing in.
 *
 * The admin panel lives behind `/login`; this page is the front door. It ships
 * inside the admin bundle rather than as a separate site so the domain serves
 * something meaningful at its root without a second deploy target to build,
 * host and keep in sync.
 */

const FEATURES = [
  {
    icon: "🎥",
    title: "Realtime vision",
    body: "Camera frames stream to the model as you move — scene understanding, OCR and reasoning about whatever you are looking at.",
  },
  {
    icon: "🎙️",
    title: "Realtime voice",
    body: "Speak naturally and hear the answer as it is generated. Interrupt mid-sentence and it stops, like a real conversation.",
  },
  {
    icon: "🌐",
    title: "Live translation",
    body: "Point it at a conversation and hear it in your language, speech to speech, metered apart from your assistant time.",
  },
  {
    icon: "🤖",
    title: "Agentic actions",
    body: "It does things, not just talks: notes, reminders and tasks, web search, and messages — decided by the model, run by a real tool engine.",
  },
  {
    icon: "🕶️",
    title: "Phone today, glasses tomorrow",
    body: "One device adapter behind the app, so the same assistant moves from your phone to smart glasses without a rewrite.",
  },
  {
    icon: "♻️",
    title: "Built to stay connected",
    body: "Heartbeats, backoff reconnects and session resume — a dropped signal in a lift resumes the conversation, it does not end it.",
  },
];

export default function Landing() {
  return (
    <div className="landing">
      <header className="landing-nav">
        <div className="brand">
          <div className="mark" />
          <span>
            Farry<em>On</em>
          </span>
        </div>
        <nav>
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <Link className="btn-primary" to="/login">
            Sign in
          </Link>
        </nav>
      </header>

      <section className="landing-hero">
        <p className="eyebrow">Realtime voice · vision · agentic AI</p>
        <h1>
          An assistant that <em>sees</em> what you see
          <br />
          and <em>talks back</em>.
        </h1>
        <p className="lede">
          Stream your camera and microphone to an AI that watches, listens, answers out loud —
          and takes action while you keep your hands free.
        </p>
        <div className="landing-cta">
          <Link className="btn-primary lg" to="/login">
            Sign in
          </Link>
          <a className="btn-outline lg" href="#features">
            See what it does
          </a>
        </div>
      </section>

      <section className="landing-section" id="features">
        <h2>What it does</h2>
        <div className="feature-grid">
          {FEATURES.map((f) => (
            <article className="feature" key={f.title}>
              <span className="feature-icon" aria-hidden="true">
                {f.icon}
              </span>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section" id="how">
        <h2>How it works</h2>
        <ol className="steps">
          {/* The text is wrapped so each step is exactly two grid children —
              the counter and one block. Left loose, the <span> would land in
              the 34px counter column and wrap a word per line. */}
          <li>
            <div>
              <b>Open the app</b>
              <span>Your phone streams microphone audio and about one camera frame a second.</span>
            </div>
          </li>
          <li>
            <div>
              <b>It watches and listens</b>
              <span>
                Audio and frames reach the model over a single live connection, so answers begin
                before you have finished asking.
              </span>
            </div>
          </li>
          <li>
            <div>
              <b>It answers and acts</b>
              <span>
                You hear the reply as it is spoken, and anything it decides to do — a note, a
                reminder, a search, a message — happens for real.
              </span>
            </div>
          </li>
        </ol>
      </section>

      <footer className="landing-foot">
        <div className="brand">
          <div className="mark" />
          <span>
            Farry<em>On</em>
          </span>
        </div>
        <span className="muted">Realtime voice, vision and agentic AI.</span>
        <Link to="/login">Admin sign in</Link>
      </footer>
    </div>
  );
}

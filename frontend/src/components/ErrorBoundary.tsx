import { Component, ErrorInfo, ReactNode } from "react";

/**
 * Stops a render error from blanking the whole page.
 *
 * React unmounts the entire tree on an uncaught render error, which shows as a
 * white screen. This catches it and shows the actual message instead — so a bug
 * degrades to a readable error, never a dead page (which matters a lot in a live
 * demo).
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface it in the console for diagnosis too.
    console.error("Render error:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-xl p-8">
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-5">
            <h2 className="text-sm font-semibold text-rose-800">Something went wrong rendering this view</h2>
            <p className="mt-1 text-xs text-rose-700">
              The rest of the app is fine — reload to continue. Details:
            </p>
            <pre className="scroll-thin mt-3 max-h-64 overflow-auto rounded bg-white p-3 font-mono text-[11px] text-rose-900 ring-1 ring-inset ring-rose-200">
              {String(this.state.error?.stack || this.state.error)}
            </pre>
            <button
              onClick={() => this.setState({ error: null })}
              className="mt-3 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white"
            >
              Dismiss
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

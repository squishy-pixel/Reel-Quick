// Timecode that never jitters: every digit sits in a cell of the same width,
// so Saira's proportional figures stop pushing the readout around.
export function Tc({ value, className = "" }: { value: string; className?: string }) {
  return (
    <span className={`tcfixed ${className}`} aria-label={value}>
      {value.split("").map((ch, i) => <span key={i} className={/\d/.test(ch) ? "d" : "s"}>{ch}</span>)}
    </span>
  );
}

// Shared, non-component values. Lives here rather than being exported from a
// feature component so that importing a constant doesn't drag a whole page
// module in with it (KpiTiles used to import this from Clean.jsx), and so
// React Fast Refresh keeps working on files that only export components.

// Maps app/data/profile.py's dataset_quality() grade to a theme colour, so
// the same score reads the same way everywhere it appears.
export const GRADE_COLORS = {
  excellent: 'var(--green)', good: 'var(--green)',
  fair: 'var(--amber)', poor: 'var(--red)',
}

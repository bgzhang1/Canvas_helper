import type { ReactNode } from 'react';

export function SidebarButton({ active, icon, onClick, children }: { active: boolean; icon: ReactNode; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-4 px-4 py-3 border transition-none font-medium text-sm tracking-wide ${
        active ? 'bg-black text-[#F4F4F0] border-black' : 'bg-[#F4F4F0] text-black border-transparent hover:border-black'
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

import { Building2, Grid3x3, Plane, RadioTower, Route, Ruler, Sparkles, Sun, Users, type LucideIcon, type LucideProps } from "lucide-react";
import { MODULE_META, type ModuleIconName, type ModuleId } from "@/lib/modules";

/** Lucide components for the icon names declared in `MODULE_META`. */
export const MODULE_ICON_COMPONENTS: Readonly<Record<ModuleIconName, LucideIcon>> = {
  Ruler,
  Plane,
  Users,
  Building2,
  Sparkles,
  Grid3x3,
  Route,
  Sun,
  RadioTower,
};

export interface ModuleIconProps extends LucideProps {
  module: ModuleId;
}

/** The decorative Lucide icon of a workspace module (always aria-hidden). */
export function ModuleIcon({ module, ...props }: ModuleIconProps) {
  const Icon = MODULE_ICON_COMPONENTS[MODULE_META[module].icon];
  return <Icon aria-hidden {...props} />;
}

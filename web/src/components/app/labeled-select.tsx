import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

/**
 * 横向筛选/选择控件的统一文案：选中后仍然显示字段名，避免只看到一个孤立的值。
 * 表单中已有明确 Label 的 Select 不需要使用这个组件。
 */
export function LabeledSelect({
  label,
  value,
  onChange,
  items,
  className,
  placeholder = "未选择",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  items: { value: string; label: string }[];
  className?: string;
  placeholder?: string;
}) {
  const selected = items.find((item) => item.value === value);
  const selectedLabel = selected?.label || placeholder;
  const accessibleName = `${label}：${selectedLabel}`;

  return (
    <Select value={value} onValueChange={(next) => onChange(next ?? value)}>
      <SelectTrigger className={cn("shrink-0", className)} aria-label={accessibleName}>
        <SelectValue>
          <span className="text-muted-foreground">{label}：</span>
          <span className="min-w-0 truncate">{selectedLabel}</span>
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            {item.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

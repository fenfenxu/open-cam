import { Maximize2, Minimize2, GripVertical } from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";

const DEFAULT_DRAWER_WIDTH = 720;
const MIN_DRAWER_WIDTH = 480;
const MAX_DRAWER_WIDTH = 1100;

function drawerWidthBounds(viewportWidth?: number | null) {
  const availableWidth =
    viewportWidth ?? (typeof window === "undefined" ? MAX_DRAWER_WIDTH + 24 : window.innerWidth);
  const max = Math.max(MIN_DRAWER_WIDTH, Math.min(MAX_DRAWER_WIDTH, availableWidth - 24));
  return { min: MIN_DRAWER_WIDTH, max };
}

function clampDrawerWidth(width: number) {
  const { min, max } = drawerWidthBounds();
  return Math.min(max, Math.max(min, width));
}

export function DetailDrawer({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
}) {
  const [drawerWidth, setDrawerWidth] = useState(DEFAULT_DRAWER_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const [viewportWidth, setViewportWidth] = useState<number | null>(null);
  const drawerWidthRef = useRef(drawerWidth);

  const updateDrawerWidth = useCallback((width: number) => {
    const nextWidth = clampDrawerWidth(width);
    drawerWidthRef.current = nextWidth;
    setDrawerWidth(nextWidth);
  }, []);

  const handlePointerMove = useCallback(
    (event: globalThis.PointerEvent) => {
      updateDrawerWidth(window.innerWidth - event.clientX);
    },
    [updateDrawerWidth],
  );

  const stopResizing = useCallback(() => {
    setIsResizing(false);
    window.removeEventListener("pointermove", handlePointerMove);
    window.removeEventListener("pointerup", stopResizing);
    window.removeEventListener("pointercancel", stopResizing);
  }, [handlePointerMove]);

  useEffect(() => {
    const handleWindowResize = () => {
      setViewportWidth(window.innerWidth);
      updateDrawerWidth(drawerWidthRef.current);
    };
    handleWindowResize();
    window.addEventListener("resize", handleWindowResize);
    return () => {
      window.removeEventListener("resize", handleWindowResize);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResizing);
      window.removeEventListener("pointercancel", stopResizing);
    };
  }, [handlePointerMove, stopResizing, updateDrawerWidth]);

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsResizing(true);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResizing);
    window.addEventListener("pointercancel", stopResizing);
  };

  const handleResizeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const { min, max } = drawerWidthBounds(viewportWidth);
    const step = event.shiftKey ? 120 : 40;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      updateDrawerWidth(drawerWidth + step);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      updateDrawerWidth(drawerWidth - step);
    } else if (event.key === "Home") {
      event.preventDefault();
      updateDrawerWidth(max);
    } else if (event.key === "End") {
      event.preventDefault();
      updateDrawerWidth(min);
    }
  };

  const { min, max } = drawerWidthBounds(viewportWidth);
  const isExpanded = drawerWidth >= max - 1;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className={`w-full max-w-none gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-none data-[side=right]:sm:w-[var(--detail-drawer-width)] data-[side=right]:sm:max-w-none ${
          isResizing ? "transition-none" : ""
        }`}
        style={{
          "--detail-drawer-width": `${drawerWidth}px`,
          maxWidth: "none",
        } as CSSProperties}
      >
        <div className="absolute top-3 right-11 z-10">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            title={isExpanded ? "收回详情抽屉" : "展开详情抽屉"}
            aria-label={isExpanded ? "收回详情抽屉" : "展开详情抽屉"}
            onClick={() => updateDrawerWidth(isExpanded ? DEFAULT_DRAWER_WIDTH : max)}
          >
            {isExpanded ? <Minimize2 /> : <Maximize2 />}
          </Button>
        </div>
        <div
          role="separator"
          tabIndex={0}
          aria-label="调整详情抽屉宽度"
          aria-orientation="vertical"
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={Math.round(drawerWidth)}
          title="拖动调整宽度；方向键微调"
          onPointerDown={handlePointerDown}
          onKeyDown={handleResizeKeyDown}
          className={`group absolute inset-y-0 left-0 z-10 flex w-3 -translate-x-1/2 cursor-col-resize touch-none items-center justify-center outline-none ${
            isResizing ? "bg-primary/10" : ""
          }`}
        >
          <GripVertical className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
        </div>
        <SheetHeader className="pr-20">
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription className="sr-only">详情抽屉</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4 pb-6">{children}</div>
      </SheetContent>
    </Sheet>
  );
}

/*
MapActionBar 组件负责在地图界面上显示一组操作按钮，
根据传入的操作数据生成对应的链接按钮，并提供样式区分不同类型的操作。
组件会根据操作数据的变化动态更新显示内容，并支持在没有操作时隐藏整个操作栏。
*/
export type MapAction = {
  key: string;
  label: string;
  href: string;
  emphasis?: "primary" | "secondary";
};

type MapActionBarProps = {
  actions: MapAction[];
};

export function MapActionBar({ actions }: MapActionBarProps) {
  if (!actions.length) {
    return null;
  }

  return (
    <div className="map-action-bar">
      {actions.map((action) => (
        <a
          key={action.key}
          href={action.href}
          target="_blank"
          rel="noreferrer"
          className={`map-action-btn ${action.emphasis === "primary" ? "is-primary" : "is-secondary"}`}
          data-testid={`map-action-${action.key}`}
        >
          {action.label}
        </a>
      ))}
    </div>
  );
}

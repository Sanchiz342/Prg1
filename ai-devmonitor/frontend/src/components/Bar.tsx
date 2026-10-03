import { metricMax, metricUnit } from "../util";

export default function Bar({ name, value }: { name: string; value: number }) {
  const width = Math.min(100, (value / metricMax(name, value)) * 100);
  const hot = ["cpu", "ram", "disk", "db_connections", "error_rate"].includes(name) && value >= 85;
  return (
    <div className="bar-row">
      <span className="bar-name">{name}</span>
      <div className="bar"><div className={`fill ${hot ? "hot" : ""}`} style={{ width: `${width}%` }} /></div>
      <span className="bar-val">{value}{metricUnit(name)}</span>
    </div>
  );
}

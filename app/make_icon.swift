// Draws the 1024x1024 app icon: a Salesforce-style cloud syncing with a sheet.
// Usage: swift make_icon.swift <output.png>
import AppKit

let size: CGFloat = 1024
let output = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon.png"

let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

func color(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255,
            green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: alpha)
}

// Background tile, inset to match the standard macOS icon grid.
let tile = NSRect(x: 100, y: 100, width: 824, height: 824)
let tilePath = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)
NSGraphicsContext.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowColor = color(0x000000, 0.3)
shadow.shadowOffset = NSSize(width: 0, height: -12)
shadow.shadowBlurRadius = 24
shadow.set()
color(0x0B5CAB).setFill()
tilePath.fill()
NSGraphicsContext.restoreGraphicsState()
NSGradient(starting: color(0x1EA7E0), ending: color(0x0B5CAB))!.draw(in: tilePath, angle: -90)

// Cloud (upper left).
let cloud = NSBezierPath()
for circle in [
    NSRect(x: 190, y: 560, width: 190, height: 190),
    NSRect(x: 290, y: 620, width: 230, height: 230),
    NSRect(x: 450, y: 580, width: 190, height: 190),
] {
    cloud.append(NSBezierPath(ovalIn: circle))
}
cloud.append(NSBezierPath(roundedRect: NSRect(x: 190, y: 560, width: 450, height: 120),
                          xRadius: 60, yRadius: 60))
color(0xFFFFFF).setFill()
cloud.fill()

// Spreadsheet card (lower right).
let sheet = NSRect(x: 470, y: 190, width: 330, height: 400)
NSGraphicsContext.saveGraphicsState()
let sheetShadow = NSShadow()
sheetShadow.shadowColor = color(0x000000, 0.25)
sheetShadow.shadowOffset = NSSize(width: 0, height: -8)
sheetShadow.shadowBlurRadius = 18
sheetShadow.set()
color(0x0F9D58).setFill()
NSBezierPath(roundedRect: sheet, xRadius: 36, yRadius: 36).fill()
NSGraphicsContext.restoreGraphicsState()

let grid = sheet.insetBy(dx: 55, dy: 70)
color(0xFFFFFF).setFill()
NSBezierPath(rect: grid).fill()
color(0x0F9D58).setFill()
let line: CGFloat = 14
for row in 1..<4 {
    let y = grid.minY + grid.height * CGFloat(row) / 4 - line / 2
    NSBezierPath(rect: NSRect(x: grid.minX, y: y, width: grid.width, height: line)).fill()
}
NSBezierPath(rect: NSRect(x: grid.minX + grid.width * 0.4 - line / 2, y: grid.minY,
                          width: line, height: grid.height)).fill()

// Sync badge (lower left): two arc arrows in a white circle.
let badgeCenter = NSPoint(x: 330, y: 340)
color(0xFFFFFF).setFill()
NSBezierPath(ovalIn: NSRect(x: badgeCenter.x - 120, y: badgeCenter.y - 120,
                            width: 240, height: 240)).fill()

func arcArrow(from startAngle: CGFloat, to endAngle: CGFloat) {
    let radius: CGFloat = 66
    let arc = NSBezierPath()
    arc.appendArc(withCenter: badgeCenter, radius: radius,
                  startAngle: startAngle, endAngle: endAngle, clockwise: false)
    arc.lineWidth = 26
    arc.lineCapStyle = .butt
    arc.stroke()

    // Arrowhead at the end of the arc, pointing counterclockwise.
    let a = endAngle * .pi / 180
    let tip = NSPoint(x: badgeCenter.x + cos(a) * radius, y: badgeCenter.y + sin(a) * radius)
    let tangent = NSPoint(x: -sin(a), y: cos(a))
    let normal = NSPoint(x: cos(a), y: sin(a))
    let head = NSBezierPath()
    head.move(to: NSPoint(x: tip.x + tangent.x * 40, y: tip.y + tangent.y * 40))
    head.line(to: NSPoint(x: tip.x + normal.x * 34, y: tip.y + normal.y * 34))
    head.line(to: NSPoint(x: tip.x - normal.x * 34, y: tip.y - normal.y * 34))
    head.close()
    head.fill()
}
color(0x0B5CAB).setStroke()
color(0x0B5CAB).setFill()
arcArrow(from: 20, to: 150)
arcArrow(from: 200, to: 330)

NSGraphicsContext.current = nil
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))

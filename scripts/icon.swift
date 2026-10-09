import AppKit
let directory = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for (name, size) in [("16x16",16),("16x16@2x",32),("32x32",32),("32x32@2x",64),("128x128",128),("128x128@2x",256),("256x256",256),("256x256@2x",512),("512x512",512),("512x512@2x",1024)] {
    let image = NSImage(size: NSSize(width: size, height: size))
    image.lockFocus()
    let scale = CGFloat(size) / 1024
    let transform = NSAffineTransform(); transform.scale(by: scale); transform.concat()
    NSColor(calibratedRed: 0.11, green: 0.16, blue: 0.09, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 50, y: 50, width: 924, height: 924), xRadius: 210, yRadius: 210).fill()
    NSColor(calibratedRed: 0.74, green: 0.87, blue: 0.63, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 142, y: 300, width: 740, height: 430), xRadius: 72, yRadius: 72).fill()
    let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 325, weight: .semibold), .foregroundColor: NSColor(calibratedRed: 0.12, green: 0.19, blue: 0.08, alpha: 1)]
    let text = "S" as NSString
    let width = text.size(withAttributes: attrs).width
    text.draw(at: NSPoint(x: (1024-width)/2, y: 316), withAttributes: attrs)
    NSColor(calibratedRed: 0.43, green: 0.56, blue: 0.34, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 364, y: 181, width: 296, height: 22), xRadius: 11, yRadius: 11).fill()
    image.unlockFocus()
    let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
    try rep.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent("icon_\(name).png"))
}

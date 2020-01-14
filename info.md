## Changelog
### 0.0.7 - Major Stability and Error fix
**WHEN UPGRADING TO THIS VERSION YOU WILL HAVE TO RETRIGGER EACH SENSOR TO HAVE IT DISPLAY AGAIN**

This is a one time thing you have to do once updating and restarting HomeAssistant. Once you do this, stability on restarts should improve dramatically. This specifically solves any errors that contain `result = ws.List()` in the error message. This is the vast majority of errors reported.
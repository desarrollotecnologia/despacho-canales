Attribute VB_Name = "ColbeefEtiquetas"
Option Explicit

' Imprime en la Zebra (Browser Print) solo las filas VISIBLES de la hoja activa
' (después de filtrar por cliente, cava, riel, etc.), ordenadas por Cava → Riel → Código.

Private Function ConfigVal(clave As String) As String
    Dim ws As Worksheet
    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("CONFIG")
    On Error GoTo 0
    If ws Is Nothing Then
        If UCase$(clave) = "API_URL" Then ConfigVal = "http://192.168.20.205:8012"
        Exit Function
    End If
    Dim r As Range, c As Long
    For c = 1 To 20
        If UCase$(Trim$(CStr(ws.Cells(c, 1).Value))) = UCase$(clave) Then
            ConfigVal = Trim$(CStr(ws.Cells(c, 2).Value))
            Exit Function
        End If
    Next c
    If UCase$(clave) = "API_URL" Then ConfigVal = "http://192.168.20.205:8012"
End Function

Private Function ColPorEncabezado(ws As Worksheet, encabezado As String) As Long
    Dim c As Long, last As Long
    last = ws.Cells(3, ws.Columns.Count).End(xlToLeft).Column
    For c = 1 To last
        If Trim$(CStr(ws.Cells(3, c).Value)) = encabezado Then
            ColPorEncabezado = c
            Exit Function
        End If
    Next c
    ColPorEncabezado = 0
End Function

Private Function EscJson(s As String) As String
    Dim t As String
    t = Replace(s, "\", "\\")
    t = Replace(t, """", "\""")
    t = Replace(t, vbCr, "")
    t = Replace(t, vbLf, "")
    EscJson = t
End Function

Private Function HttpReq(method As String, url As String, Optional body As String = "", Optional contentType As String = "application/json") As String
    Dim http As Object
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open method, url, False
    If Len(body) > 0 Then
        http.setRequestHeader "Content-Type", contentType
        http.send body
    Else
        http.send
    End If
    If http.Status < 200 Or http.Status >= 300 Then
        Err.Raise vbObjectError + 1, , "HTTP " & http.Status & " · " & Left$(http.responseText, 300)
    End If
    HttpReq = http.responseText
End Function

Private Function JsonGetString(json As String, key As String) As String
    ' Extrae "key":"valor" o "key": { ... } de forma simple (solo strings planos / zpl)
    Dim p As Long, q As Long, marker As String
    marker = """" & key & """"
    p = InStr(1, json, marker, vbTextCompare)
    If p = 0 Then Exit Function
    p = InStr(p, json, ":")
    If p = 0 Then Exit Function
    Do While p < Len(json) And Mid$(json, p + 1, 1) = " "
        p = p + 1
    Loop
    If Mid$(json, p + 1, 1) = """" Then
        p = p + 2
        q = p
        Do While q <= Len(json)
            If Mid$(json, q, 1) = "\" Then
                q = q + 2
            ElseIf Mid$(json, q, 1) = """" Then
                JsonGetString = Replace(Replace(Mid$(json, p, q - p), "\n", vbLf), "\""", """")
                JsonGetString = Replace(JsonGetString, "\\", "\")
                Exit Function
            Else
                q = q + 1
            End If
        Loop
    End If
End Function

Private Sub EnviarZplBrowserPrint(zpl As String)
    Dim defResp As String, deviceJson As String, i As Long, parte As String, body As String
    Dim parts As Collection, arr() As String, s As String
    On Error GoTo falló
    defResp = HttpReq("GET", "http://127.0.0.1:9100/default?type=printer")
    If Len(Trim$(defResp)) = 0 Then Err.Raise vbObjectError + 2, , "Browser Print no devolvió impresora. Ábrelo y conecta la Zebra."
    deviceJson = Trim$(defResp)

    Set parts = New Collection
    arr = Split(Replace(zpl, "^XA", vbFormFeed & "^XA"), vbFormFeed)
    For i = LBound(arr) To UBound(arr)
        s = Trim$(arr(i))
        If Left$(s, 3) = "^XA" Then parts.Add s
    Next i
    If parts.Count = 0 Then parts.Add zpl

    For i = 1 To parts.Count
        parte = parts(i)
        body = "{""device"":" & deviceJson & ",""data"":" & JsonQuote(parte) & "}"
        Call HttpReq("POST", "http://127.0.0.1:9100/write", body, "text/plain;charset=UTF-8")
    Next i
    Exit Sub
falló:
    Err.Raise Err.Number, , "Zebra Browser Print: " & Err.Description & vbCrLf & "Abre Browser Print, conecta la Zebra y reintenta."
End Sub

Private Function JsonQuote(s As String) As String
    Dim t As String, i As Long, ch As String, out As String
    out = """"
    For i = 1 To Len(s)
        ch = Mid$(s, i, 1)
        Select Case ch
            Case "\": out = out & "\\"
            Case """": out = out & "\"""
            Case vbCr: ' skip
            Case vbLf: out = out & "\n"
            Case vbTab: out = out & "\t"
            Case Else: out = out & ch
        End Select
    Next i
    JsonQuote = out & """"
End Function

Public Sub ImprimirFilasVisibles()
    Dim ws As Worksheet, rng As Range, area As Range, row As Range
    Dim colCod As Long, colCava As Long, colRiel As Long
    Dim codigos As Collection, i As Long, j As Long
    Dim api As String, body As String, resp As String, zpl As String, marcas As String
    Dim items() As String, n As Long, tmp As String, swapped As Boolean
    Dim cavaA As String, cavaB As String, rielA As String, rielB As String, codA As String, codB As String

    Set ws = ActiveSheet
    If ws Is Nothing Then
        MsgBox "No hay hoja activa.", vbExclamation
        Exit Sub
    End If
    If UCase$(ws.Name) = "CONFIG" Or UCase$(ws.Name) = "PORTADA" Then
        MsgBox "Abre la hoja GENERAL o la de un OPL, filtra y vuelve a ejecutar.", vbInformation
        Exit Sub
    End If

    colCod = ColPorEncabezado(ws, "Código")
    colCava = ColPorEncabezado(ws, "Cava")
    colRiel = ColPorEncabezado(ws, "Riel")
    If colCod = 0 Then
        MsgBox "No encuentro la columna «Código» en la fila 3.", vbExclamation
        Exit Sub
    End If

    On Error Resume Next
    Set rng = ws.Range(ws.Cells(3, 1), ws.Cells(ws.Rows.Count, colCod).End(xlUp)).SpecialCells(xlCellTypeVisible)
    On Error GoTo 0
    If rng Is Nothing Then
        MsgBox "No hay filas visibles. Quita filtros o deja al menos una fila.", vbExclamation
        Exit Sub
    End If

    Set codigos = New Collection
    For Each area In rng.Areas
        For Each row In area.Rows
            If row.Row >= 4 Then
                Dim cod As String, cav As String, rie As String
                cod = Trim$(CStr(ws.Cells(row.Row, colCod).Value))
                If Len(cod) > 0 Then
                    cav = "": rie = ""
                    If colCava > 0 Then cav = Trim$(CStr(ws.Cells(row.Row, colCava).Value))
                    If colRiel > 0 Then rie = Trim$(CStr(ws.Cells(row.Row, colRiel).Value))
                    codigos.Add cav & vbTab & rie & vbTab & cod
                End If
            End If
        Next row
    Next area

    n = codigos.Count
    If n = 0 Then
        MsgBox "No hay códigos en las filas visibles.", vbExclamation
        Exit Sub
    End If

    ReDim items(1 To n)
    For i = 1 To n
        items(i) = codigos(i)
    Next i
    ' Orden burbuja: cava, riel, código
    Do
        swapped = False
        For i = 1 To n - 1
            Dim p1() As String, p2() As String
            p1 = Split(items(i), vbTab)
            p2 = Split(items(i + 1), vbTab)
            cavaA = UCase$(p1(0)): rielA = UCase$(p1(1)): codA = p1(2)
            cavaB = UCase$(p2(0)): rielB = UCase$(p2(1)): codB = p2(2)
            If cavaA > cavaB Or (cavaA = cavaB And rielA > rielB) Or (cavaA = cavaB And rielA = rielB And codA > codB) Then
                tmp = items(i): items(i) = items(i + 1): items(i + 1) = tmp
                swapped = True
            End If
        Next i
    Loop While swapped

    If MsgBox("¿Imprimir " & n & " etiqueta(s) visibles (orden cava → riel) en la Zebra de este equipo?", vbYesNo + vbQuestion, "Colbeef · Etiquetas") <> vbYes Then Exit Sub

    api = ConfigVal("API_URL")
    If Right$(api, 1) = "/" Then api = Left$(api, Len(api) - 1)

    body = "{""codigos"":["
    For i = 1 To n
        If i > 1 Then body = body & ","
        body = body & """" & EscJson(Split(items(i), vbTab)(2)) & """"
    Next i
    body = body & "],""copias"":1,""solo_zpl"":true}"

    On Error GoTo errHttp
    resp = HttpReq("POST", api & "/api/etiqueta/imprimir", body)
    zpl = JsonGetString(resp, "zpl")
    If Len(zpl) = 0 Then
        MsgBox "El servidor no devolvió ZPL." & vbCrLf & Left$(resp, 400), vbCritical
        Exit Sub
    End If

    Call EnviarZplBrowserPrint(zpl)

    ' Marcar impresas (best-effort)
    On Error Resume Next
    Dim marcasBody As String, codList As String
    codList = ""
    For i = 1 To n
        If i > 1 Then codList = codList & ","
        codList = codList & "{""codigo"":""" & EscJson(Split(items(i), vbTab)(2)) & """,""fecha"":""" & EscJson(ConfigVal("FECHA")) & """}"
    Next i
    marcasBody = "{""marcas"":[" & codList & "]}"
    If Len(ConfigVal("FECHA")) > 0 Then
        Call HttpReq("POST", api & "/api/etiquetas/marcar", marcasBody)
    End If
    On Error GoTo 0

    MsgBox "✅ Enviadas " & n & " etiqueta(s) a la Zebra (Browser Print).", vbInformation, "Colbeef"
    Exit Sub

errHttp:
    MsgBox "No se pudo imprimir:" & vbCrLf & Err.Description, vbCritical, "Colbeef · Etiquetas"
End Sub

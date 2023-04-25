#!/usr/bin/env python3

# SPDX-FileCopyrightText: © 2022 Decompollaborate
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Callable
import rabbitizer

from ... import common


class SymbolBase(common.ElementBase):
    def __init__(self, context: common.Context, vromStart: int, vromEnd: int, inFileOffset: int, vram: int, words: list[int], sectionType: common.FileSectionType, segmentVromStart: int, overlayCategory: str|None):
        super().__init__(context, vromStart, vromEnd, inFileOffset, vram, "", words, sectionType, segmentVromStart, overlayCategory)

        self.endOfLineComment: dict[int, str] = dict()
        # offset in words

        self.contextSym = self.addSymbol(self.vram, sectionType=self.sectionType, isAutogenerated=True)
        self.contextSym.vromAddress = self.vromStart
        self.contextSym.isDefined = True
        self.contextSym.sectionType = self.sectionType

        self.stringEncoding: str = "EUC-JP"
        self._failedStringDecoding: bool = False

        self.relocs: dict[int, common.RelocationInfo] = dict()
        "key: word offset"


    def getName(self) -> str:
        return self.contextSym.getName()

    def getNameEnd(self) -> str|None:
        return self.contextSym.getNameEnd()

    def setNameIfUnset(self, name: str) -> None:
        self.contextSym.setNameIfUnset(name)

    def setNameGetCallback(self, callback: Callable[[common.ContextSymbol], str]) -> None:
        self.contextSym.setNameGetCallback(callback)

    def setNameGetCallbackIfUnset(self, callback: Callable[[common.ContextSymbol], str]) -> None:
        self.contextSym.setNameGetCallbackIfUnset(callback)


    def canUseAddendsOnData(self) -> bool:
        if self.contextSym.notAllowedToReferenceAddends:
            return False

        if common.GlobalConfig.ALLOW_ALL_ADDENDS_ON_DATA:
            return True

        return self.contextSym.allowedToReferenceAddends

    def canUseConstantsOnData(self) -> bool:
        if self.contextSym.notAllowedToReferenceConstants:
            return False

        if common.GlobalConfig.ALLOW_ALL_CONSTANTS_ON_DATA:
            return True

        return self.contextSym.allowedToReferenceConstants


    def generateAsmLineComment(self, localOffset: int, wordValue: int|None = None) -> str:
        if not common.GlobalConfig.ASM_COMMENT:
            return ""

        offsetHex = "{0:0{1}X}".format(localOffset + self.inFileOffset + self.commentOffset, common.GlobalConfig.ASM_COMMENT_OFFSET_WIDTH)

        currentVram = self.getVramOffset(localOffset)
        vramHex = f"{currentVram:08X}"

        wordValueHex = ""
        if wordValue is not None:
            wordValueHex = f"{common.Utils.wordToCurrenEndian(wordValue):08X} "

        return f"/* {offsetHex} {vramHex} {wordValueHex}*/"


    def getSymbolAsmDeclaration(self, symName: str, useGlobalLabel: bool=True) -> str:
        if not useGlobalLabel:
            return f"{symName}:" + common.GlobalConfig.LINE_ENDS

        output = ""
        output += self.getLabelFromSymbol(self.contextSym, symName)
        if self.sectionType == common.FileSectionType.Text:
            if common.GlobalConfig.ASM_TEXT_ENT_LABEL:
                output += f"{common.GlobalConfig.ASM_TEXT_ENT_LABEL} {symName}{common.GlobalConfig.LINE_ENDS}"

            if common.GlobalConfig.ASM_TEXT_FUNC_AS_LABEL:
                output += f"{symName}:{common.GlobalConfig.LINE_ENDS}"
        else:
            if common.GlobalConfig.ASM_DATA_SYM_AS_LABEL:
                output += f"{symName}:{common.GlobalConfig.LINE_ENDS}"
        return output

    def getExtraLabelFromSymbol(self, contextSym: common.ContextSymbol|None) -> str:
        label = ""
        if contextSym is not None:
            label = common.GlobalConfig.LINE_ENDS
            symLabel = contextSym.getLabelMacro()
            if symLabel is not None:
                label += f"{symLabel} {contextSym.getName()}{common.GlobalConfig.LINE_ENDS}"
                if common.GlobalConfig.ASM_DATA_SYM_AS_LABEL:
                    label += f"{contextSym.getName()}:" + common.GlobalConfig.LINE_ENDS
        return label

    def getReloc(self, wordOffset: int, instr: rabbitizer.Instruction|None) -> common.RelocationInfo | None:
        relocInfo = self.context.globalRelocationOverrides.get(self.getVromOffset(wordOffset))

        if relocInfo is None:
            relocInfo = self.relocs.get(wordOffset)

        return relocInfo

    def relocToInlineStr(self, relocInfo: common.RelocationInfo | None) -> str:
        if relocInfo is None:
            return ""
        return relocInfo.getInlineStr()

    def isByte(self, index: int) -> bool:
        return self.contextSym.isByte() and not self.isString()

    def isShort(self, index: int) -> bool:
        return self.contextSym.isShort()

    def isString(self) -> bool:
        return self.contextSym.isString() and not self._failedStringDecoding

    def isFloat(self, index: int) -> bool:
        if self.contextSym.isFloat():
            if index >= len(self.words):
                return False
            word = self.words[index]
            # Filter out NaN and infinity
            if (word & 0x7F800000) != 0x7F800000:
                return True
        return False

    def isDouble(self, index: int) -> bool:
        if self.contextSym.isDouble():
            if index + 1 < len(self.words):
                word0 = self.words[index]
                word1 = self.words[index+1]
                # Filter out NaN and infinity
                if (((word0 << 32) | word1) & 0x7FF0000000000000) != 0x7FF0000000000000:
                    # Prevent accidentally losing symbols
                    currentVram = self.getVramOffset(index*4)
                    if self.getSymbol(currentVram+4, tryPlusOffset=False) is None:
                        return True
        return False

    def isJumpTable(self) -> bool:
        return False


    def isRdata(self) -> bool:
        "Checks if the current symbol is .rdata"
        return False

    def shouldMigrate(self) -> bool:
        return False


    def renameBasedOnType(self):
        if not common.GlobalConfig.AUTOGENERATED_NAMES_BASED_ON_DATA_TYPE:
            return

        if not self.contextSym.isAutogenerated:
            return

        if self.isFloat(0):
            self.contextSym.name = f"FLT_{self.vram:08X}"
        elif self.isDouble(0):
            self.contextSym.name = f"DBL_{self.vram:08X}"
        elif self.isString():
            self.contextSym.name = f"STR_{self.vram:08X}"


    def analyze(self):
        self.renameBasedOnType()

        byteStep = 4
        if self.contextSym.isByte():
            byteStep = 1
        elif self.contextSym.isShort():
            byteStep = 2

        if self.sectionType != common.FileSectionType.Bss:
            for i in range(0, self.sizew):
                localOffset = 4*i
                for j in range(0, 4, byteStep):
                    if i == 0 and j == 0:
                        continue

                    # Possible symbols in the middle of words
                    currentVram = self.getVramOffset(localOffset+j)
                    contextSym = self.getSymbol(currentVram, tryPlusOffset=False)
                    if contextSym is not None:
                        contextSym.vromAddress = self.getVromOffset(localOffset+j)
                        contextSym.isDefined = True
                        contextSym.sectionType = self.sectionType
                        contextSym.setTypeIfUnset(self.contextSym.getTypeSpecial(), self.contextSym.isAutogenerated)

                if byteStep == 4:
                    word = self.words[i]
                    referencedSym = self.getSymbol(word, tryPlusOffset=False)
                    if referencedSym is not None:
                        referencedSym.referenceSymbols.add(self.contextSym)


    def getEndOfLineComment(self, wordIndex: int) -> str:
        if not common.GlobalConfig.ASM_COMMENT:
            return ""

        return self.endOfLineComment.get(wordIndex, "")

    def getJByteAsByte(self, i: int, j: int) -> str:
        localOffset = 4*i
        w = self.words[i]

        dotType = ".byte"

        shiftValue = j * 8
        if common.GlobalConfig.ENDIAN == common.InputEndian.BIG:
            shiftValue = 24 - shiftValue
        subVal = (w & (0xFF << shiftValue)) >> shiftValue
        value = f"0x{subVal:02X}"

        comment = self.generateAsmLineComment(localOffset+j)
        return f"{comment} {dotType} {value}"

    def getJByteAsShort(self, i: int, j: int) -> str:
        localOffset = 4*i
        w = self.words[i]

        dotType = ".short"

        shiftValue = j * 8
        if common.GlobalConfig.ENDIAN == common.InputEndian.BIG:
            shiftValue = 16 - shiftValue
        subVal = (w & (0xFFFF << shiftValue)) >> shiftValue
        value = f"0x{subVal:04X}"

        comment = self.generateAsmLineComment(localOffset+j)
        return f"{comment} {dotType} {value}"

    def getNthWordAsBytesAndShorts(self, i : int, sym1: common.ContextSymbol|None, sym2: common.ContextSymbol|None, sym3: common.ContextSymbol|None) -> tuple[str, int]:
        output = ""

        if sym1 is not None or self.isByte(i) or (not self.isShort(i) and sym3 is not None):
            output += self.getJByteAsByte(i, 0)
            output += common.GlobalConfig.LINE_ENDS

            output += self.getExtraLabelFromSymbol(sym1)
            output += self.getJByteAsByte(i, 1)
            output += common.GlobalConfig.LINE_ENDS
        else:
            output += self.getJByteAsShort(i, 0)
            output += common.GlobalConfig.LINE_ENDS

        output += self.getExtraLabelFromSymbol(sym2)
        if sym3 is not None or (sym2 is not None and sym2.isByte()) or (self.isByte(i) and (sym2 is None or not sym2.isShort())):
            output += self.getJByteAsByte(i, 2)
            output += common.GlobalConfig.LINE_ENDS

            output += self.getExtraLabelFromSymbol(sym3)
            output += self.getJByteAsByte(i, 3)
            output += common.GlobalConfig.LINE_ENDS
        else:
            output += self.getJByteAsShort(i, 2)
            output += common.GlobalConfig.LINE_ENDS

        return output, 0


    def _allowWordSymbolReference(self, symbolRef: common.ContextSymbol, word: int) -> bool:
        if symbolRef.isElfNotype:
            return False

        symType = symbolRef.getTypeSpecial()
        if isinstance(symType, common.SymbolSpecialType):
            if symType == common.SymbolSpecialType.function:
                if word != symbolRef.vram:
                    # Avoid using addends on functions
                    return False

            if symType.isTargetLabel():
                if word != symbolRef.vram:
                    # Avoid using addends on labels
                    return False

                if not self.contextSym.isJumpTable():
                    # Non jumptables should not reference labels
                    return False

        return True

    def getNthWordAsWords(self, i: int, canReferenceSymbolsWithAddends: bool=False, canReferenceConstants: bool=False) -> tuple[str, int]:
        output = ""
        localOffset = 4*i
        vram = self.getVramOffset(localOffset)
        w = self.words[i]

        dotType = ".word"

        label = ""
        if i != 0:
            label = self.getExtraLabelFromSymbol(self.getSymbol(vram, tryPlusOffset=False))

        value = f"0x{w:08X}"

        # .elf relocated symbol
        relocInfo = self.getReloc(localOffset, None)
        if relocInfo is not None:
            if relocInfo.staticReference is not None:
                relocVram = relocInfo.staticReference.sectionVram + w
                contextSym = self.getSymbol(relocVram, checkUpperLimit=False)
                if contextSym is not None:
                    value = contextSym.getSymbolPlusOffset(relocVram)
            else:
                value = relocInfo.getName()
        else:
            # This word could be a reference to a symbol
            symbolRef = self.getSymbol(w, tryPlusOffset=canReferenceSymbolsWithAddends)
            if symbolRef is not None and not self.context.isAddressBanned(symbolRef.vram):
                if self._allowWordSymbolReference(symbolRef, w):
                    value = symbolRef.getSymbolPlusOffset(w)
            elif canReferenceConstants:
                constant = self.getConstant(w)
                if constant is not None:
                    value = constant.getName()

        comment = self.generateAsmLineComment(localOffset)
        output += f"{label}{comment} {dotType} {value}"
        output += self.getEndOfLineComment(i)
        output += common.GlobalConfig.LINE_ENDS

        return output, 0

    def getNthWordAsFloat(self, i: int) -> tuple[str, int]:
        output = ""
        localOffset = 4*i
        vram = self.getVramOffset(localOffset)
        w = self.words[i]

        label = ""
        if i != 0:
            label = self.getExtraLabelFromSymbol(self.getSymbol(vram, tryPlusOffset=False))

        dotType = ".float"
        floatValue = common.Utils.wordToFloat(w)
        value = f"{floatValue:.10g}"

        comment = self.generateAsmLineComment(localOffset, w)
        output += f"{label}{comment} {dotType} {value}"
        output += self.getEndOfLineComment(i)
        output += common.GlobalConfig.LINE_ENDS

        return output, 0

    def getNthWordAsDouble(self, i: int) -> tuple[str, int]:
        output = ""
        localOffset = 4*i
        vram = self.getVramOffset(localOffset)
        w = self.words[i]

        label = ""
        if i != 0:
            label = self.getExtraLabelFromSymbol(self.getSymbol(vram, tryPlusOffset=False))

        dotType = ".double"
        otherHalf = self.words[i+1]
        doubleWord = (w << 32) | otherHalf
        doubleValue = common.Utils.qwordToDouble(doubleWord)
        value = f"{doubleValue:.18g}"

        comment = self.generateAsmLineComment(localOffset, doubleWord)
        output += f"{label}{comment} {dotType} {value}"
        output += self.getEndOfLineComment(i)
        output += common.GlobalConfig.LINE_ENDS

        return output, 1

    def getNthWordAsString(self, i: int) -> tuple[str, int]:
        localOffset = 4*i

        buffer = bytearray(4*len(self.words))
        common.Utils.wordsToBytes(self.words, buffer)
        decodedStrings, rawStringSize = common.Utils.decodeString(buffer, localOffset, self.stringEncoding)

        # To be a valid aligned string, the next word-aligned bytes needs to be zero
        checkStartOffset = localOffset + rawStringSize
        checkEndOffset = min((checkStartOffset & ~3) + 4, len(buffer))
        while checkStartOffset < checkEndOffset:
            if buffer[checkStartOffset] != 0:
                raise RuntimeError()
            checkStartOffset += 1

        skip = rawStringSize // 4
        comment = self.generateAsmLineComment(localOffset)
        result = f"{comment} "

        commentPaddingNum = 22
        if not common.GlobalConfig.ASM_COMMENT:
            commentPaddingNum = 1

        if rawStringSize == 0:
            decodedStrings.append("")
        for decodedValue in decodedStrings[:-1]:
            result += f'.ascii "{decodedValue}"'
            result += common.GlobalConfig.LINE_ENDS + (commentPaddingNum * " ")
        result += f'.asciz "{decodedStrings[-1]}"{common.GlobalConfig.LINE_ENDS}'

        return result, skip

    def getNthWord(self, i: int, canReferenceSymbolsWithAddends: bool=False, canReferenceConstants: bool=False) -> tuple[str, int]:
        return self.getNthWordAsWords(i, canReferenceSymbolsWithAddends=canReferenceSymbolsWithAddends, canReferenceConstants=canReferenceConstants)


    def countExtraPadding(self) -> int:
        "Returns how many extra word paddings this symbol has"
        return 0

    def getPrevAlignDirective(self, i: int=0) -> str:
        if self.parent is not None and self.parent.vram % 0x8 != 0:
            # Can't emit alignment directives if the parent file isn't properly aligned
            return ""

        if self.isDouble(i):
            if common.GlobalConfig.COMPILER in {common.Compiler.SN64, common.Compiler.PSYQ}:
                # This should be harmless in other compilers
                # TODO: investigate if it is fine to use it unconditionally
                return f".align 3{common.GlobalConfig.LINE_ENDS}"
        elif self.isJumpTable():
            if i == 0 and common.GlobalConfig.COMPILER not in {common.Compiler.IDO, common.Compiler.PSYQ}:

                if self.vram % 0x8 == 0:
                    return f".align 3{common.GlobalConfig.LINE_ENDS}"

        return ""

    def getPostAlignDirective(self, i: int=0) -> str:
        commentPaddingNum = 22
        if not common.GlobalConfig.ASM_COMMENT:
            commentPaddingNum = 1

        alignDirective = ""

        if self.isString():
            alignDirective += commentPaddingNum * " "
            if common.GlobalConfig.COMPILER in {common.Compiler.SN64, common.Compiler.PSYQ}:
                alignDirective += ".align 2"
            else:
                alignDirective += ".balign 4"
            alignDirective += common.GlobalConfig.LINE_ENDS

        return alignDirective

    def disassembleAsData(self, useGlobalLabel: bool=True) -> str:
        output = self.contextSym.getReferenceeSymbols()
        output += self.getPrevAlignDirective(0)

        symName = self.getName()
        output += self.getSymbolAsmDeclaration(symName, useGlobalLabel)

        canReferenceSymbolsWithAddends = self.canUseAddendsOnData()
        canReferenceConstants = self.canUseConstantsOnData()

        i = 0
        while i < self.sizew:
            vram = self.getVramOffset(i*4)

            sym1 = self.getSymbol(vram+1, tryPlusOffset=False, checkGlobalSegment=False)
            sym2 = self.getSymbol(vram+2, tryPlusOffset=False, checkGlobalSegment=False)
            sym3 = self.getSymbol(vram+3, tryPlusOffset=False, checkGlobalSegment=False)

            # Check for symbols in the middle of this word
            if sym1 is not None or sym2 is not None or sym3 is not None or self.isByte(i) or self.isShort(i):
                data, skip = self.getNthWordAsBytesAndShorts(i, sym1, sym2, sym3)
            elif self.isFloat(i):
                data, skip = self.getNthWordAsFloat(i)
            elif self.isDouble(i):
                data, skip = self.getNthWordAsDouble(i)
            elif self.isString():
                try:
                    data, skip = self.getNthWordAsString(i)
                except (UnicodeDecodeError, RuntimeError):
                    # Not a string
                    self._failedStringDecoding = True
                    data, skip = self.getNthWord(i, canReferenceSymbolsWithAddends, canReferenceConstants)
            else:
                data, skip = self.getNthWord(i, canReferenceSymbolsWithAddends, canReferenceConstants)

            if i != 0:
                output += self.getPrevAlignDirective(i)
            output += data
            if common.GlobalConfig.EMIT_INLINE_RELOC:
                relocInfo = self.getReloc(i*4, None)
                output += self.relocToInlineStr(relocInfo)
            output += self.getPostAlignDirective(i)

            i += skip
            i += 1

        if common.GlobalConfig.ASM_EMIT_SIZE_DIRECTIVE:
            output += f".size {symName}, . - {symName}{common.GlobalConfig.LINE_ENDS}"

        nameEnd = self.getNameEnd()
        if nameEnd is not None:
            output += self.getSymbolAsmDeclaration(nameEnd, useGlobalLabel)

        return output

    def disassemble(self, migrate: bool=False, useGlobalLabel: bool=True) -> str:
        output = ""

        if migrate:
            output += self.getSpimdisasmVersionString()

        output = self.disassembleAsData(useGlobalLabel=useGlobalLabel)
        return output

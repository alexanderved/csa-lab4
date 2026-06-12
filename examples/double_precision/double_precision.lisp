(defvar stage 0)

(defvar ahi 0)
(defvar alo 0)
(defvar bhi 0)
(defvar blo 0)

(defvar rhi 0)
(defvar rlo 0)

(defun read-numbers ()
    (declare (interrupt 0))
    (if (= stage 0)
        (setq ahi (input 0)))
    (if (= stage 1)
        (setq alo (input 0)))
    (if (= stage 2)
        (setq bhi (input 0)))
    (if (= stage 3)
        (setq blo (input 0)))
    (setq stage (+ stage 1)))

(defun wait-vars ()
    (if (< stage 4) (wait-vars)))

(defun sum-long (ahi alo bhi blo)
    (setq rlo (+ alo blo))
    (setq rhi (+c ahi bhi)))

(wait-vars)
(sum-long ahi alo bhi blo)
(output 2 rhi)
(output 2 rlo)